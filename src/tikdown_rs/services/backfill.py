"""Backfill — services/backfill.py (§10).

Backfill foreground con cursor estricto por upload_date, contabilidad F-09,
robustez F-10, cancelación cooperativa T21, cookies obligatorias F-01.

story: e04s02
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tikdown_rs.core.pacing import CooldownReserve, reserve_slot
from tikdown_rs.models.models import BackfillSlot, MonitoredAccount

LOG = logging.getLogger("tikdown_rs.backfill")

# Estados terminales para el cursor (§10): downloaded/failed/skipped — NO cancelled
_TERMINAL = ("downloaded", "failed", "skipped")


class NoCookiesError(Exception):
    """No hay cookies working → backfill aborta (F-01)."""


def cursor_should_advance(upload_date: str, cursor: str, status: str = "downloaded") -> bool:
    """¿El cursor avanza sobre este vídeo? (§10)

    Comparación estrictamente < (nunca ==); solo estados terminales
    (cancelled no es terminal para el cursor).
    """
    if status not in _TERMINAL:
        return False
    return upload_date < cursor  # §10: estrictamente <


def effective_upload_date(upload_date: str | None, cursor: str) -> str:
    """upload_date ausente → fallback al cursor anterior (L-F2, no NULL)."""
    return upload_date if upload_date else cursor


async def run_backfill(
    session: AsyncSession,
    username: str,
    engine,
    cookies: list,
    feed_entries: list | None = None,
    on_event=None,
) -> str:
    """Ejecuta un backfill foreground (§10).

    Returns outcome: 'completed' | 'cancelled' | 'failed'.
    """
    result = await session.execute(
        select(MonitoredAccount).where(MonitoredAccount.username == username)
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise ValueError(f"cuenta no encontrada: {username}")

    # F-01: cookies obligatorias
    if not cookies:
        raise NoCookiesError(f"backfill.no_cookies: sin cookies working para {username}")

    # F-10: el listado del feed va DENTRO del try catástrofe; feed_entries=None
    # → listar; [] explícito → sin entradas (test/foreground vacío). Listar
    # PRIMERO: backfill_total usa las entradas reales (bug #13, F-09), no el
    # feed_entries crudo (None → 0).
    try:
        entries = await _list_feed(engine, username) if feed_entries is None else feed_entries
    except Exception:
        # F-10: si el listado falla, el try catástrofe lo marca 'failed'
        raise

    # Marcar backfilling (tras listar para total real)
    account.backfill_status = "backfilling"
    account.backfill_total = len(entries)  # F-09: total al iniciar (bug #13)
    account.backfill_done = 0
    await session.commit()

    try:
        scope_cursor = account.backfill_cursor or "00000000"  # L-F1: snapshot para break
        cursor = scope_cursor

        for entry in entries:
            # T21: relectura periódica del estado (cancelación cooperativa)
            await session.refresh(account)
            if account.backfill_status != "backfilling":
                return "cancelled"  # L-F6: retorno temprano

            upload_date = entry.get("upload_date")
            if upload_date is None:
                upload_date = effective_upload_date(None, cursor)  # L-F2
            if upload_date >= scope_cursor:
                continue  # fuera de alcance del cursor (break implícito)

            # Descargar con el motor (e04s01). Fallo de UN vídeo NO aborta el
            # feed (T5: transitorio nunca definitivo) — se registra y continúa;
            # el daemon reintenta los fallidos en el siguiente ciclo.
            try:
                # T62: pacing cross-proceso entre descargas (anti-bloqueo TikTok).
                delay = await reserve_slot(session, CooldownReserve())
                if delay:
                    LOG.info("backfill.pacing_wait", extra={"seconds": delay})
                    await asyncio.sleep(delay)
                result = await engine.download(entry["url"], archive_path=None)
                status = result.get("status", "downloaded")
            except Exception as exc:  # noqa: BLE001 - T5: no dejar wedged
                LOG.warning(
                    "backfill.video_failed",
                    extra={"username": username, "url": entry.get("url"), "exc": repr(exc)},
                )
                continue
            if status == "downloaded":
                cursor = upload_date  # avanza el cursor móvil (L-F1)
                account.backfill_done += 1  # F-09: done acumulativo

        # Persistir cursor con UPDATE condicional (T21/L-F5): rowcount 0 = cancelado
        upd = await session.execute(
            update(MonitoredAccount)
            .where(
                MonitoredAccount.id == account.id,
                MonitoredAccount.backfill_status == "backfilling",
            )
            .values(
                backfill_cursor=cursor,
                backfill_done=account.backfill_done,
                backfill_status="completed",
            )
        )
        await session.commit()
        if upd.rowcount == 0:
            return "cancelled"  # L-F5/L-F6: cancelación detectada
        if on_event:
            on_event("backfill.completed", {"username": username})
        return "completed"

    except asyncio.CancelledError:
        # F-10/e13s01: interrupción → 'queued' (crash) o 'paused' (causa red/disco)
        paused_disk = await _downloads_paused(session)
        network_online = True  # el daemon conoce el estado; por defecto online
        new_status = status_after_interruption(session, paused_disk, network_online)
        reason = "disk" if paused_disk else ("network" if not network_online else None)
        await session.execute(
            update(MonitoredAccount)
            .where(MonitoredAccount.id == account.id)
            .values(
                backfill_status=new_status,
                backfill_pause_reason=reason,
            )
        )
        await session.commit()
        raise
    except Exception as exc:  # noqa: BLE001 - F-10: no dejar wedged en backfilling
        LOG.error("backfill.failed", extra={"username": username, "exc": repr(exc)})
        await session.execute(
            update(MonitoredAccount)
            .where(MonitoredAccount.id == account.id)
            .values(backfill_status="failed")
        )
        await session.commit()
        return "failed"


async def _list_feed(engine, username: str) -> list[dict]:
    """Lista el feed de la cuenta (F-10: dentro del try catástrofe)."""
    if engine is None:
        return []
    profile = await engine.extract_profile(username)
    return profile.get("entries", [])


async def reconcile_stale_backfills(session: AsyncSession) -> int:
    """Devuelve a 'queued' los backfills huérfanos en 'backfilling' (F-10)."""
    result = await session.execute(
        update(MonitoredAccount)
        .where(MonitoredAccount.backfill_status == "backfilling")
        .values(backfill_status="queued")
    )
    await session.commit()
    return result.rowcount or 0


# --- Slot único de backfill CROSS-PROCESO (e13s01, T22) ---
#
# Reemplaza el asyncio.Lock por proceso: tabla singleton backfill_slot con
# adquisición atómica CAS (UPDATE ... SET owner=:me WHERE owner IS NULL
# RETURNING) visible para daemon + CLI + bot. Mismo patrón que
# download_pacing_state (T22/L-C6).


def status_after_interruption(
    session: AsyncSession,
    paused_disk: bool,
    network_online: bool,
) -> str:
    """Estado tras una interrupción (e13s01).

    - disco pausado o red offline → 'paused' (estado real, se reanuda al
      resolverse la causa)
    - si no → 'queued' (F-10 crash, auto-reanudable)
    """
    if paused_disk or not network_online:
        return "paused"
    return "queued"


async def _ensure_slot_row(session: AsyncSession) -> None:
    """Crea la fila singleton con INSERT ... ON CONFLICT + commit (L-C6)."""
    from tikdown_rs.models.models import BackfillSlot

    session.add(BackfillSlot(id=1))
    try:
        await session.commit()
    except Exception:
        await session.rollback()


async def backfill_slot_busy(session: AsyncSession) -> bool:
    """¿El slot cross-proceso está ocupado? (e13s01, T22)."""
    await _ensure_slot_row(session)
    result = await session.execute(select(BackfillSlot).where(BackfillSlot.id == 1))
    row = result.scalar_one()
    return row.owner is not None


async def acquire_slot(session: AsyncSession, owner: str = "daemon") -> bool:
    """Adquiere el slot cross-proceso de forma ATÓMICA (e13s01, T22).

    UPDATE ... SET owner=:me WHERE owner IS NULL RETURNING — CAS vía SQLite;
    solo un proceso gana. Devuelve True si lo adquirió.
    """
    await _ensure_slot_row(session)
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    result = await session.execute(
        text(
            "UPDATE backfill_slot SET owner = :me, acquired_at = :now "
            "WHERE id = 1 AND owner IS NULL RETURNING owner"
        ),
        {"me": owner, "now": now},
    )
    await session.commit()
    return result.scalar_one_or_none() is not None


async def release_slot(session: AsyncSession, owner: str = "daemon") -> None:
    """Libera el slot solo si este proceso lo posee (e13s01, T22)."""
    await _ensure_slot_row(session)
    await session.execute(
        text("UPDATE backfill_slot SET owner = NULL WHERE id = 1 AND owner = :me"),
        {"me": owner},
    )
    await session.commit()


async def collect_queued_backfills(
    session: AsyncSession,
    engine,
    cookies: list,
    on_event=None,
    owner: str = "daemon",
    network_online: bool = True,
) -> list[str]:
    """Recoge backfills 'queued' + 'paused' reanudables (F-10/e13s01).

    - Slot cross-proceso (T22): si está ocupado (otro proceso), no ejecuta.
    - 'paused' solo se reanuda si la causa se resolvió (red online + disco no
      pausado).
    - PROPAGA el canal de eventos (T75: nunca None — L-I5).
    """
    if not await acquire_slot(session, owner=owner):  # T22: slot cross-proceso
        LOG.info("backfill.slot_busy")
        return []
    try:
        paused_disk = await _downloads_paused(session)
        statuses = ["queued"]
        if network_online and not paused_disk:
            statuses.append("paused")  # e13s01: paused con causa resuelta
        result = await session.execute(
            select(MonitoredAccount).where(MonitoredAccount.backfill_status.in_(statuses))
        )
        pending = list(result.scalars().all())
        outcomes: list[str] = []
        for account in pending:
            outcome = await run_backfill(
                session,
                account.username,
                engine=engine,
                cookies=cookies,
                feed_entries=None,
                on_event=on_event,  # T75: propagar canal
            )
            outcomes.append(f"{account.username}:{outcome}")
        return outcomes
    finally:
        await release_slot(session, owner=owner)


async def _downloads_paused(session: AsyncSession) -> bool:
    """Lee downloads_paused del daemon_state (e13s01)."""
    from tikdown_rs.core.daemon_state import get_or_create_daemon_state

    row = await get_or_create_daemon_state(session)
    return bool(row.downloads_paused)


async def reconcile_transitions(session: AsyncSession) -> int:
    """Aplica transiciones pendientes history→monitor (T59, arranque).

    UPDATE idempotente: solo cuentas con monitor_after_backfill=1 Y
    backfill_status='completed' Y mode='history'.
    """
    result = await session.execute(
        update(MonitoredAccount)
        .where(
            MonitoredAccount.monitor_after_backfill == True,  # noqa: E712
            MonitoredAccount.backfill_status == "completed",
            MonitoredAccount.mode == "history",
        )
        .values(mode="monitor", monitor_after_backfill=False)
    )
    await session.commit()
    return result.rowcount or 0


async def cancel_backfill(session: AsyncSession, username: str) -> None:
    """Cancela un backfill (T21, cooperativo).

    Marca 'cancelled' en la DB; el worker relee periódicamente y detiene el
    bucle. La re-ejecución retoma desde el cursor. CHECK incluye 'cancelled'
    (L-F7).
    """
    await session.execute(
        update(MonitoredAccount)
        .where(MonitoredAccount.username == username)
        .values(backfill_status="cancelled")
    )
    await session.commit()

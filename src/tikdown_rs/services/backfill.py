"""Backfill — services/backfill.py (§10).

Backfill foreground con cursor estricto por upload_date, contabilidad F-09,
robustez F-10, cancelación cooperativa T21, cookies obligatorias F-01.

story: e04s02
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tikdown_rs.models.models import MonitoredAccount

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

    # Marcar backfilling
    account.backfill_status = "backfilling"
    account.backfill_total = len(feed_entries or [])  # F-09: total al iniciar
    account.backfill_done = 0
    await session.commit()

    try:
        # F-10: el listado del feed va DENTRO del try catástrofe
        # feed_entries=None → listar; [] explícito → sin entradas (test/foreground vacío)
        entries = await _list_feed(engine, username) if feed_entries is None else feed_entries
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

            # Descargar con el motor (e04s01)
            result = await engine.download(entry["url"], archive_path=None)
            status = result.get("status", "downloaded")
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
        # F-10: interrupción → vuelve a queued (auto-reanudable)
        await session.execute(
            update(MonitoredAccount)
            .where(MonitoredAccount.id == account.id)
            .values(backfill_status="queued")
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


# Slot único de backfill activo por proceso (§10) — adquisición no bloqueante
_slot_lock: asyncio.Lock | None = None


def _get_slot() -> asyncio.Lock:
    global _slot_lock
    if _slot_lock is None:
        _slot_lock = asyncio.Lock()
    return _slot_lock


def backfill_slot_busy() -> bool:
    """¿El slot único está ocupado? (F-10, §10) — solo comprobación.

    La adquisición real es async (acquire_slot). Comprobar nunca adquiere:
    si está libre devuelve False y el llamador decide si adquirir.
    """
    return _get_slot().locked()


async def acquire_slot() -> bool:
    """Adquiere el slot de forma NO bloqueante (F-10/§10).

    if lock.locked(): return False; await lock.acquire(); return True.
    """
    lock = _get_slot()
    if lock.locked():
        return False  # ocupado, no bloquear
    await lock.acquire()
    return True


def _release_slot() -> None:
    lock = _get_slot()
    if lock.locked():
        lock.release()


async def collect_queued_backfills(
    session: AsyncSession,
    engine,
    cookies: list,
    on_event=None,
) -> list[str]:
    """Recoge backfills 'queued' y los ejecuta con su slot (F-10/T75).

    Comprueba backfill_slot_busy() antes de crear la tarea (F-10) y PROPAGA
    el canal de eventos a run_backfill (T75: nunca None — L-I5).
    """
    if not await acquire_slot():  # F-10: comprobar antes de crear la tarea
        LOG.info("backfill.slot_busy")
        return []
    try:
        result = await session.execute(
            select(MonitoredAccount).where(MonitoredAccount.backfill_status == "queued")
        )
        queued = list(result.scalars().all())
        outcomes: list[str] = []
        for account in queued:
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
        _release_slot()


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

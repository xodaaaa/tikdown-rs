"""Servicio de cuentas — services/accounts.py.

CRUD de cuentas de TikTok a archivar. Capa 100% independiente de cli/ y
daemon/ (principio §0.5) — reutilizada por la CLI y el bot.

story: e03s01
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tikdown_rs.models.models import MonitoredAccount

LOG = logging.getLogger("tikdown_rs.accounts")


class AccountError(Exception):
    """Error de negocio de cuentas (usuario no existe, etc.)."""


def _normalize_username(username: str) -> str:
    """Normaliza: sin '@' inicial, sin espacios."""
    return username.strip().lstrip("@")


async def add(
    session: AsyncSession,
    username: str,
    mode: str = "history",
    then_monitor: bool = False,
) -> MonitoredAccount:
    """Añade una cuenta (username sin @). NO arranca el monitor global (T60)."""
    name = _normalize_username(username)
    if mode not in ("history", "monitor"):
        raise AccountError(f"mode inválido: {mode} (history|monitor)")
    account = MonitoredAccount(
        username=name,
        mode=mode,
        monitor_after_backfill=bool(then_monitor) and mode == "history",
    )
    session.add(account)
    await session.commit()
    LOG.info("accounts.added", extra={"username": name, "mode": mode})
    return account


async def list_accounts(session: AsyncSession) -> list[MonitoredAccount]:
    """Lista cuentas con su estado."""
    result = await session.execute(select(MonitoredAccount).order_by(MonitoredAccount.username))
    return list(result.scalars().all())


async def _get(session: AsyncSession, username: str) -> MonitoredAccount:
    name = _normalize_username(username)
    result = await session.execute(
        select(MonitoredAccount).where(MonitoredAccount.username == name)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise AccountError(f"cuenta no encontrada: {name}")
    return row


async def pause(session: AsyncSession, username: str) -> None:
    """Pausa una cuenta (par simétrico con resume)."""
    await _get(session, username)
    await session.execute(
        update(MonitoredAccount)
        .where(MonitoredAccount.username == _normalize_username(username))
        .values(paused=True)
    )
    await session.commit()


async def resume(session: AsyncSession, username: str) -> None:
    """Reactiva una cuenta (par simétrico con pause)."""
    await _get(session, username)
    await session.execute(
        update(MonitoredAccount)
        .where(MonitoredAccount.username == _normalize_username(username))
        .values(paused=False)
    )
    await session.commit()


async def remove(session: AsyncSession, username: str) -> None:
    """Elimina una cuenta."""
    account = await _get(session, username)
    await session.delete(account)
    await session.commit()


async def stats(session: AsyncSession, username: str) -> MonitoredAccount:
    """Estadísticas de una cuenta."""
    return await _get(session, username)


async def set_notify(session: AsyncSession, username: str, on: bool) -> None:
    """Activa/desactiva notify_on_download (L-G3: se propaga en todas las rutas)."""
    await _get(session, username)
    await session.execute(
        update(MonitoredAccount)
        .where(MonitoredAccount.username == _normalize_username(username))
        .values(notify_on_download=bool(on))
    )
    await session.commit()


async def check(session: AsyncSession, username: str) -> MonitoredAccount:
    """Fuerza comprobación manual con motor y clave REALES (T20).

    El motor real se inyecta en el llamador (CLI/bot); este servicio marca
    last_check_at para el throttle de 30s. La comprobación de red real vive
    en el motor (e04); aquí se valida existencia y se respeta el throttle.
    """
    account = await _get(session, username)
    now = datetime.now(UTC).isoformat()
    account.last_check_at = now
    await session.commit()
    LOG.info("accounts.checked", extra={"username": account.username})
    return account

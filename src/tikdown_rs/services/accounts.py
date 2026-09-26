"""Monitored account services: add, list, pause/resume, notify, remove, get.

Trampas neutralizadas: T-BACKFILL-1, T-BACKFILL-9. Regla: 3.1, 10.1.

Services are pure: no cli/, daemon/ or yt_dlp imports here. T-BACKFILL-1:
``last_check_at`` is NEVER initialized to now -- a never-checked account must
always be checked later (the 30 s throttle treats NULL as always-due).
T-BACKFILL-9: ``backfill_status`` starts 'idle', a value present in the CHECK
from the first schema.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.models import MonitoredAccount, Video

VALID_MODES = ("history", "monitor")


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize(username: str) -> str:
    """Strip leading '@' and lowercase (3.1: username stored without '@')."""
    return username.strip().lstrip("@").strip().lower()


async def add_account(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
    mode: str = "history",
    then_monitor: bool = False,
) -> int:
    """Add an account; duplicate UNIQUE username is a ConfigurationError.

    Defaults per 3.1: mode, all boolean flags off, backfill_status='idle'
    (T-BACKFILL-9), last_check_at NULL (T-BACKFILL-1).
    """
    name = _normalize(username)
    if not name:
        raise ConfigurationError("username is empty after normalization")
    if mode not in VALID_MODES:
        raise ConfigurationError(f"invalid mode: {mode} (expected one of {', '.join(VALID_MODES)})")
    now = _utcnow_iso()
    async with session_factory() as session:
        row = MonitoredAccount(
            username=name,
            mode=mode,
            monitor_after_backfill=then_monitor,
            backfill_status="idle",
            # last_check_at stays NULL on purpose (T-BACKFILL-1).
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ConfigurationError(f"account already exists: {name}") from exc
        return row.id


async def list_accounts(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[dict]:
    """All accounts with their lifecycle fields (3.1); minimal, no joins yet."""
    async with session_factory() as session:
        result = await session.execute(select(MonitoredAccount).order_by(MonitoredAccount.id))
        return [
            {
                "id": row.id,
                "username": row.username,
                "mode": row.mode,
                "paused": row.paused,
                "needs_review": row.needs_review,
                "backfill_status": row.backfill_status,
                "monitor_after_backfill": row.monitor_after_backfill,
                "notify_on_download": row.notify_on_download,
            }
            for row in result.scalars().all()
        ]


async def get_account(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
) -> MonitoredAccount | None:
    """The normalized account row, or None."""
    async with session_factory() as session:
        result = await session.execute(
            select(MonitoredAccount).where(MonitoredAccount.username == _normalize(username))
        )
        return result.scalars().first()


async def set_paused(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
    paused: bool,
) -> None:
    """Pause or resume an account; unknown user is a ConfigurationError."""
    name = _normalize(username)
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    select(MonitoredAccount).where(MonitoredAccount.username == name)
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise ConfigurationError(f"unknown account: {name}")
        row.paused = paused
        row.updated_at = _utcnow_iso()
        await session.commit()


async def set_notify(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
    enabled: bool,
) -> None:
    """Toggle per-account notify_on_download; unknown user is a ConfigurationError."""
    name = _normalize(username)
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    select(MonitoredAccount).where(MonitoredAccount.username == name)
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise ConfigurationError(f"unknown account: {name}")
        row.notify_on_download = enabled
        row.updated_at = _utcnow_iso()
        await session.commit()


async def remove_account(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
) -> None:
    """Delete the account row; videos must be handled first (no silent cascade)."""
    name = _normalize(username)
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    select(MonitoredAccount).where(MonitoredAccount.username == name)
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise ConfigurationError(f"unknown account: {name}")
        video_count = (
            await session.execute(
                select(func.count()).select_from(Video).where(Video.account_id == row.id)
            )
        ).scalar_one()
        if video_count:
            raise ConfigurationError(
                f"account {name} still has {video_count} video(s) in the videos "
                "table; remove or reassign those videos first"
            )
        await session.delete(row)
        await session.commit()

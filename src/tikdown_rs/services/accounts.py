"""Monitored account services: add, list, pause/resume, notify, remove, get, stats.

Trampas neutralizadas: T-BACKFILL-1, T-BACKFILL-9, T-DATA-10. Regla: 3.1, 10.1.

Services are pure: no cli/, daemon/ or yt_dlp imports here. T-BACKFILL-1:
``last_check_at`` is NEVER initialized to now -- a never-checked account must
always be checked later (the 30 s throttle treats NULL as always-due).
T-BACKFILL-9: ``backfill_status`` starts 'idle', a value present in the CHECK
from the first schema.
"""

from dataclasses import dataclass
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


@dataclass(frozen=True)
class AccountStat:
    """Per-account read-only aggregate (§10.1 `accounts stats`, §10.3 Column 2).

    Plain JSON-safe types so any frontend (CLI, bot, dashboard) can serialize
    it. T-DATA-10: ``total_disk_bytes`` is READ from the column, never
    recomputed from the filesystem; it approximates consumed network traffic
    (excludes failed retries and feed-listing traffic) and callers label it
    as an approximation.
    """

    username: str
    mode: str
    paused: bool
    needs_review: bool
    videos_downloaded: int
    videos_failed: int
    videos_pending: int
    total_disk_bytes: int
    backfill_status: str
    backfill_done: int
    backfill_total: int


async def account_stats(
    session_factory: async_sessionmaker[AsyncSession],
    username: str | None = None,
) -> list[AccountStat]:
    """Read-only per-account aggregates; all accounts, or one when named.

    §10.1 `accounts stats` takes no arguments, so the whole-library call is
    the primary path; the optional ``username`` exists for parity with the
    other account services. T-DATA-10: no filesystem access, no recompute.
    """
    async with session_factory() as session:
        query = select(MonitoredAccount).order_by(MonitoredAccount.username)
        if username is not None:
            query = query.where(MonitoredAccount.username == _normalize(username))
        accounts = (await session.execute(query)).scalars().all()
        if not accounts:
            return []
        status_counts = {
            (account_id, status): count
            for account_id, status, count in (
                await session.execute(
                    select(Video.account_id, Video.status, func.count())
                    .where(Video.account_id.in_([account.id for account in accounts]))
                    .group_by(Video.account_id, Video.status)
                )
            ).all()
        }
    return [
        AccountStat(
            username=account.username,
            mode=account.mode,
            paused=bool(account.paused),
            needs_review=bool(account.needs_review),
            videos_downloaded=status_counts.get((account.id, "downloaded"), 0),
            videos_failed=status_counts.get((account.id, "failed"), 0),
            videos_pending=status_counts.get((account.id, "pending"), 0),
            total_disk_bytes=account.total_disk_bytes,
            backfill_status=account.backfill_status,
            backfill_done=account.backfill_done,
            backfill_total=account.backfill_total,
        )
        for account in accounts
    ]

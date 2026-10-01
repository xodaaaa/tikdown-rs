"""Backfill state operations: queue, cancel, retry-failed, progress view (10.1).

Trampas neutralizadas: T-BACKFILL-19, T-BACKFILL-12 (entry-point cookies note),
T-ENGINE-18. Regla: 9.4, 9.6, 10.1.

Layering (4.8): no cli/, daemon/ or yt_dlp imports. The CLI builds the engine
with a REAL working cookie's blob at the entry point (T-BACKFILL-12: never a
hardcoded empty cookies list) and calls run_backfill; this module only moves
backfill_state and video rows. ``retry_failed`` receives the DownloadArchive
INJECTED (same pattern as run_backfill) and DISCARDS each retried video's
archive entry BEFORE the re-download (T-ENGINE-18: otherwise yt-dlp answers
'already downloaded').

M3 scope note (9.6): retry-failed delivers the state reset ('failed' ->
'pending' for transient/integrity categories; 'definitive' is permanent) plus
the archive discard. The re-download itself happens when the account's
backfill is run/queued again: run_backfill's pending flow picks the rows up.
"""

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.archive import DownloadArchive
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.core.timeutil import utcnow_iso as _utcnow_iso
from tikdown_rs.models import MonitoredAccount
from tikdown_rs.services.accounts import get_account

#: States a (re)queue may start from: anything except a live run or a
#: terminal result (T-BACKFILL-19: re-queueing completed/failed works;
#: re-queueing 'backfilling' is refused).
_QUEUEABLE = ("idle", "completed", "failed", "cancelled", "paused", "queued")
#: Cancel is legal from any non-terminal state (9.4); 'cancelled' itself is
#: non-terminal, so it is an idempotent no-op.
_TERMINAL_BACKFILL = ("completed", "failed")
_RETRIABLE_CATEGORIES = ("transient", "integrity")


async def queue_backfill(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
    queue: bool,
) -> str:
    """Put the account's backfill in 'queued'; returns the new status.

    Both the --queue path and the foreground run pass through here (the
    foreground CLI then runs it in-process after the gates). A 'backfilling'
    account is REFUSED either way (T-BACKFILL-19): a running backfill cannot
    be re-run or re-queued. 'queued' is an idempotent no-op. Re-queueing from
    'paused' clears backfill_pause_reason (the cause is stale once requeued).
    """
    account = await get_account(session_factory, username)
    if account is None:
        raise ConfigurationError(f"unknown account: {username}")
    status = account.backfill_status
    if status == "queued":
        return "queued"
    if status == "backfilling" or status not in _QUEUEABLE:
        raise ConfigurationError(
            f"backfill.backfilling: cannot re-queue account '{account.username}' "
            f"while its backfill_status is '{status}' (T-BACKFILL-19)"
        )
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE monitored_accounts SET backfill_status = 'queued',"
                " backfill_pause_reason = NULL, updated_at = :now"
                " WHERE id = :id"
            ),
            {"now": _utcnow_iso(), "id": account.id},
        )
        await session.commit()
    return "queued"


async def cancel_backfill(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
) -> str:
    """Mark the backfill 'cancelled' from any non-terminal state (9.4).

    The worker's periodic re-read does the cooperative stop (T-BACKFILL-10);
    this only sets the flag. Terminal states are refused with the state named.
    """
    account = await get_account(session_factory, username)
    if account is None:
        raise ConfigurationError(f"unknown account: {username}")
    status = account.backfill_status
    if status in _TERMINAL_BACKFILL:
        raise ConfigurationError(
            f"backfill already terminal: account '{account.username}' backfill_status is '{status}'"
        )
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE monitored_accounts SET backfill_status = 'cancelled',"
                " backfill_pause_reason = NULL, updated_at = :now"
                " WHERE id = :id"
            ),
            {"now": _utcnow_iso(), "id": account.id},
        )
        await session.commit()
    return "cancelled"


async def retry_failed(
    session_factory: async_sessionmaker[AsyncSession],
    username: str | None,
    all_accounts: bool,
    *,
    archive: DownloadArchive,
) -> int:
    """Reset retriable failed videos to 'pending'; returns the re-queued count.

    Exactly one of ``username`` or ``all_accounts=True``. Only 'transient' and
    'integrity' failures are retried; 'definitive' failures are permanent
    auth/content errors and stay untouched. retry_count is historical and is
    KEPT. Each retried video's download_archive entry is discarded BEFORE the
    re-download (T-ENGINE-18). See the module docstring for the M3 scope: the
    re-download happens when the account's backfill runs again.
    """
    if (username is None) != bool(all_accounts):
        raise ConfigurationError("retry-failed needs exactly one of @user or --all")
    async with session_factory() as session:
        if all_accounts:
            accounts = (
                (await session.execute(select(MonitoredAccount).order_by(MonitoredAccount.id)))
                .scalars()
                .all()
            )
        else:
            account = await get_account(session_factory, username)
            if account is None:
                raise ConfigurationError(f"unknown account: {username}")
            accounts = [account]

    count = 0
    for account in accounts:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, tiktok_video_id FROM videos"
                        " WHERE account_id = :account_id AND status = 'failed'"
                        " AND error_category IN ('transient', 'integrity')"
                    ),
                    {"account_id": account.id},
                )
            ).all()
        for row_id, video_id in rows:
            await archive.remove(video_id)  # T-ENGINE-18: BEFORE re-download
            async with session_factory() as session:
                await session.execute(
                    text(
                        "UPDATE videos SET status = 'pending', updated_at = :now"
                        " WHERE id = :id AND status = 'failed'"
                    ),
                    {"now": _utcnow_iso(), "id": row_id},
                )
                await session.commit()
            count += 1
    return count


async def backfill_status_view(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
) -> dict:
    """Progress view for `backfill status` (9.3): stored columns, plain dict."""
    account = await get_account(session_factory, username)
    if account is None:
        raise ConfigurationError(f"unknown account: {username}")
    return {
        "username": account.username,
        "backfill_status": account.backfill_status,
        "backfill_total": account.backfill_total,
        "backfill_done": account.backfill_done,
        "backfill_cursor": account.backfill_cursor,
        "pause_reason": account.backfill_pause_reason,
        "needs_review": int(account.needs_review),
    }

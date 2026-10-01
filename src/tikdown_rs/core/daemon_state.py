"""daemon_state singleton mutators: each commits internally, reads use the active session.

Trampas neutralizadas: T-DB-3, T-DB-12, T-DB-13, T-CLI-6, T-CLI-7. Regla: 3.5, 3.7, 5.3.
Queries always take the caller's active session, never the sessionmaker (T-DB-3);
writes that may hit an absent singleton row use native SQLite upserts (T-DB-12).
"""

import logging
import os

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.core.timeutil import utcnow_iso as _utcnow_iso
from tikdown_rs.models.daemon_state import DaemonState

logger = logging.getLogger("tikdown_rs.core.daemon_state")

_ROW_ID = 1


def _upsert(values: dict[str, object]):
    """INSERT ... ON CONFLICT(id) DO UPDATE for the singleton row (T-DB-12)."""
    return (
        sqlite_insert(DaemonState)
        .values(id=_ROW_ID, **values)
        .on_conflict_do_update(index_elements=["id"], set_=values)
    )


async def clear_stop_requested(session: AsyncSession) -> None:
    """Clear stop_requested, committing immediately (T-CLI-7, T-DB-13).

    Upsert, so an absent singleton row is fine (T-DB-12).
    """
    await session.execute(_upsert({"stop_requested": False}))
    await session.commit()


async def read_stop_requested(session: AsyncSession) -> bool:
    """Read stop_requested from the active session (absent row reads False)."""
    row = await session.get(DaemonState, _ROW_ID)
    return bool(row.stop_requested) if row is not None else False


async def write_heartbeat(
    session: AsyncSession,
    pid: int | None = None,
    db_busy_count: int | None = None,
    supervised_tasks: int | None = None,
    ytdlp_zombie_threads: int | None = None,
) -> None:
    """Persist last_heartbeat_at (+ pid, 5.6 contention window, M9 process
    counters), committing (T-DB-13). The counters stay untouched when their
    argument is None: a NULL column reads 'n/a (in-process)' in status.
    """
    values: dict[str, object] = {"last_heartbeat_at": _utcnow_iso()}
    if pid is not None:
        values["daemon_pid"] = pid
    if db_busy_count is not None:
        values["db_busy_count_5min"] = db_busy_count
    if supervised_tasks is not None:
        values["supervised_tasks"] = supervised_tasks
    if ytdlp_zombie_threads is not None:
        values["ytdlp_zombie_threads"] = ytdlp_zombie_threads
    await session.execute(_upsert(values))
    await session.commit()


async def register_daemon_start(session: AsyncSession, pid: int | None = None) -> None:
    """Startup bookkeeping: daemon_pid + daemon_started_at; monitor_running stays 0 (5.1)."""
    values: dict[str, object] = {
        "daemon_pid": os.getpid() if pid is None else pid,
        "daemon_started_at": _utcnow_iso(),
        "monitor_running": False,
    }
    await session.execute(_upsert(values))
    await session.commit()


async def clear_daemon_runtime(session: AsyncSession) -> None:
    """Shutdown cleanup: daemon_pid=NULL, last_heartbeat_at=NULL, monitor_running=0 (5.2).

    Also clears stop_requested: the shutdown it requested has been processed
    (T-CLI-6 in-process path); a stale flag must never survive into the next run.
    """
    values: dict[str, object] = {
        "daemon_pid": None,
        "last_heartbeat_at": None,
        "monitor_running": False,
        "stop_requested": False,
    }
    await session.execute(_upsert(values))
    await session.commit()


async def record_selfcheck(session: AsyncSession, ok: bool, degraded_reason: str | None) -> None:
    """Persist the last selfcheck result, committing immediately (T-DB-13).

    Upsert, so an absent singleton row is fine (T-DB-12). `degraded_reason` is
    NULL when ok, else a short ASCII cause like 'impersonation: curl_cffi-missing'.
    """
    await session.execute(
        _upsert(
            {
                "last_selfcheck_at": _utcnow_iso(),
                "last_selfcheck_ok": ok,
                "degraded_reason": degraded_reason,
            }
        )
    )
    await session.commit()


async def record_startup_probes(
    session: AsyncSession,
    *,
    ffmpeg_ok: bool,
    ffprobe_ok: bool,
    impersonation_reason: str | None = None,
    cookies_ok: bool = True,
) -> None:
    """Persist startup-probe degraded_reason ONLY (5.1 step 7): NULL or joined cause.

    The selfcheck fields (last_selfcheck_at/ok) are NOT touched here: only the
    periodic selfcheck job writes them -- startup degradation and periodic
    selfcheck are different producers (T-DB-13 discipline).
    """
    causes: list[str] = []
    missing = [name for name, ok in (("ffmpeg", ffmpeg_ok), ("ffprobe", ffprobe_ok)) if not ok]
    if missing:
        causes.append(f"binaries: {' and '.join(missing)} not found")
    if impersonation_reason is not None:
        causes.append(f"impersonation: {impersonation_reason}")
    if not cookies_ok:
        causes.append("cookies: none working")
    await session.execute(_upsert({"degraded_reason": "; ".join(causes) or None}))
    await session.commit()


async def record_last_known_good_ytdlp(session: AsyncSession, version: str) -> None:
    """Persist the last yt-dlp version that PASSED selfcheck (2.1, T-ENGINE-28)."""
    await session.execute(_upsert({"last_known_good_ytdlp_version": version}))
    await session.commit()


async def read_status(session: AsyncSession) -> DaemonState | None:
    """Read the singleton row for daemon status/healthcheck (None when never started)."""
    return await session.get(DaemonState, _ROW_ID)


async def set_downloads_paused(session: AsyncSession, paused: bool, reason: str = "disk") -> None:
    """Set downloads_paused, committing immediately (T-DB-13, 8.2).

    T-ENGINE-27: the reason ('disk' watermark/ENOSPC or 'manual' resume) is
    logged for the operator; the singleton schema carries no reason column,
    so it is not persisted. The disk path is currently the ONLY writer of
    this flag, which is what makes the 8.2 auto-resume unambiguous.
    """
    await session.execute(_upsert({"downloads_paused": paused}))
    await session.commit()
    logger.info("downloads_paused=%s (reason=%s)", int(paused), reason)


async def request_daemon_stop(session: AsyncSession) -> None:
    """daemon stop helper: refuse when nothing is running, else set the flag (T-CLI-6).

    Writing stop_requested for a daemon_pid IS NULL row hands the operator a false
    success: nobody is polling the flag. The refusal is an actionable ConfigurationError.
    """
    row = await session.get(DaemonState, _ROW_ID)
    if row is None or row.daemon_pid is None:
        raise ConfigurationError(
            "No running daemon to stop: daemon_state has daemon_pid NULL. "
            "Start it first with 'tikdown-rs daemon run'."
        )
    row.stop_requested = True
    await session.commit()

"""daemon_state singleton mutators: each commits internally, reads use the active session.

Trampas neutralizadas: T-DB-3, T-DB-12, T-DB-13, T-CLI-6, T-CLI-7. Regla: 3.5, 3.7, 5.3.
Queries always take the caller's active session, never the sessionmaker (T-DB-3);
writes that may hit an absent singleton row use native SQLite upserts (T-DB-12).
"""

import os
from datetime import UTC, datetime

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.models.daemon_state import DaemonState

_ROW_ID = 1


def _utcnow_iso() -> str:
    """ISO8601 UTC timestamp with explicit offset (daemon_state timestamp columns)."""
    return datetime.now(UTC).isoformat()


def _upsert(values: dict[str, object]):
    """INSERT ... ON CONFLICT(id) DO UPDATE for the singleton row (T-DB-12)."""
    return (
        sqlite_insert(DaemonState)
        .values(id=_ROW_ID, **values)
        .on_conflict_do_update(index_elements=["id"], set_=values)
    )


async def set_stop_requested(session: AsyncSession) -> None:
    """Set stop_requested=1, committing immediately (T-DB-13)."""
    await session.execute(_upsert({"stop_requested": True}))
    await session.commit()


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


async def write_heartbeat(session: AsyncSession, pid: int | None = None) -> None:
    """Persist last_heartbeat_at (and daemon_pid bookkeeping), committing (T-DB-13)."""
    values: dict[str, object] = {"last_heartbeat_at": _utcnow_iso()}
    if pid is not None:
        values["daemon_pid"] = pid
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


async def read_status(session: AsyncSession) -> DaemonState | None:
    """Read the singleton row for daemon status/healthcheck (None when never started)."""
    return await session.get(DaemonState, _ROW_ID)


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

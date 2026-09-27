"""Monitor lifecycle state service: daemon_state.monitor_running start/stop (3.3, 4.1).

Trampas neutralizadas: T-DB-12 (upsert on the absent singleton row), T-DB-13
(mutators commit immediately), 4.1 (a degraded daemon REJECTS `monitor start`
with an actionable error naming the cause and the selfcheck fix path).
Reglas: 3.3, 4.1, 5.8, 10.1.

Both mutators are what `tikdown-rs monitor start|stop` (10.1) and the future
M5 bot call; the daemon heartbeat re-reads the flag every beat (T-ASYNC-16)
and launches the cycle when it flips to 1 (hot start). Events go through the
SYNC channel (T-BACKFILL-15) and fire ONLY on a real transition: idempotent
re-invocation emits nothing.

Layering (4.8): nothing here imports cli/, daemon/ or yt_dlp.
"""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.core.notifications.events import (
    EVENT_MONITOR_STARTED,
    EVENT_MONITOR_STOPPED,
)
from tikdown_rs.models import DaemonState
from tikdown_rs.services.monitor import set_monitor_running

_ROW_ID = 1

#: 4.1 remediation: every degraded rejection names the cause AND this fix path.
DEGRADED_REMEDIATION = (
    "run 'tikdown-rs daemon selfcheck' for details, fix the cause, "
    "then restart the daemon ('tikdown-rs daemon run')"
)


def degraded_error(reason: str) -> ConfigurationError:
    """One actionable 4.1 error: cause + remediation, shared by monitor/backfill gates."""
    return ConfigurationError(
        f"daemon is degraded ({reason}); downloads are disabled. {DEGRADED_REMEDIATION}"
    )


def _emit(on_event: Callable | None, event: str) -> None:
    """Fire the SYNC channel (T-BACKFILL-15); None channel is a no-op."""
    if on_event is not None:
        on_event({"event": event})


async def _read_state(session_factory: async_sessionmaker[AsyncSession]) -> DaemonState | None:
    async with session_factory() as session:
        return await session.get(DaemonState, _ROW_ID)


async def start_monitor(session_factory, on_event: Callable | None = None) -> None:
    """Set daemon_state.monitor_running=1, committing immediately (3.3, T-DB-13).

    Order (4.1): the degradation gate runs FIRST -- a degraded daemon has no
    working download path, so `monitor start` is refused with the cause and
    the selfcheck remediation, and the flag is never written. Idempotent:
    already 1 -> no-op, no duplicate event. On transition, `monitor.started`
    is emitted through the SYNC channel.
    """
    state = await _read_state(session_factory)
    if state is not None and state.degraded_reason:
        raise degraded_error(state.degraded_reason)
    if state is not None and state.monitor_running:
        return
    await set_monitor_running(session_factory, True)
    _emit(on_event, EVENT_MONITOR_STARTED)


async def stop_monitor(session_factory, on_event: Callable | None = None) -> None:
    """Set daemon_state.monitor_running=0, committing immediately (T-DB-13).

    Idempotent: already 0 -> no-op, no duplicate event. On transition,
    `monitor.stopped` is emitted through the SYNC channel.
    """
    state = await _read_state(session_factory)
    if state is not None and not state.monitor_running:
        return
    await set_monitor_running(session_factory, False)
    _emit(on_event, EVENT_MONITOR_STOPPED)

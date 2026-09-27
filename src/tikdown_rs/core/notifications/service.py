"""Synchronous notification channel (6.2, T-BACKFILL-15).

The emission channel is a SYNC callable injected into jobs. T-BACKFILL-15:
never wrap it in ``async def`` -- the coroutine would be created and never
run, losing events in silence. ``NoopNotificationService`` is the default
(6.2); the persistent spool and the real send are OUT of scope (17.1).
"""

import time
from typing import NamedTuple, Protocol, runtime_checkable


@runtime_checkable
class NotificationService(Protocol):
    """The one channel contract: synchronous, best-effort, never raises."""

    def emit(self, event: str, payload: dict) -> None: ...


class NoopNotificationService:
    """Default service (6.2): the base scope emits nothing."""

    def emit(self, event: str, payload: dict) -> None:
        return None


class EmittedEvent(NamedTuple):
    """One recorded emission: event name, payload copy, wall-clock timestamp."""

    event: str
    payload: dict
    timestamp: float


class InMemoryNotificationService:
    """Test double: records emitted events in order, in memory only."""

    def __init__(self) -> None:
        self.events: list[EmittedEvent] = []

    def emit(self, event: str, payload: dict) -> None:
        self.events.append(EmittedEvent(event, dict(payload), time.time()))

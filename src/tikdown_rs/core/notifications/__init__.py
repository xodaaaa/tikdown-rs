"""Notification contract (6.2): catalog, render, synchronous channel.

Base scope = the event catalog + ``render`` + a SYNC emission channel with
``NoopNotificationService`` as the default. The persistent spool and the real
send are OUT of scope (17.1): this package is the contract that keeps the
future push epic cheap.
"""

from .events import (
    EVENT_BACKFILL_CANCELLED,
    EVENT_BACKFILL_COMPLETED,
    EVENT_BACKFILL_NO_COOKIES,
    EVENT_BACKFILL_PAUSED,
    EVENT_BACKFILL_STARTED,
    EVENT_DOWNLOAD_DOWNLOADED,
    EVENT_DOWNLOAD_FAILED,
    EVENT_DOWNLOAD_SKIPPED,
    EVENTS,
    EventSpec,
    render,
)
from .service import (
    EmittedEvent,
    InMemoryNotificationService,
    NoopNotificationService,
    NotificationService,
)

__all__ = [
    "EVENTS",
    "EVENT_BACKFILL_CANCELLED",
    "EVENT_BACKFILL_COMPLETED",
    "EVENT_BACKFILL_NO_COOKIES",
    "EVENT_BACKFILL_PAUSED",
    "EVENT_BACKFILL_STARTED",
    "EVENT_DOWNLOAD_DOWNLOADED",
    "EVENT_DOWNLOAD_FAILED",
    "EVENT_DOWNLOAD_SKIPPED",
    "EmittedEvent",
    "EventSpec",
    "InMemoryNotificationService",
    "NoopNotificationService",
    "NotificationService",
    "render",
]

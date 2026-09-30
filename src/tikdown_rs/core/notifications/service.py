"""Synchronous notification channel (6.2, T-BACKFILL-15).

The emission channel is a SYNC callable injected into jobs. T-BACKFILL-15:
never wrap it in ``async def`` -- the coroutine would be created and never
run, losing events in silence. ``NoopNotificationService`` is the default
(6.2); the persistent spool and the real send are OUT of scope (17.1).
"""

import asyncio
import html
import logging
import time
from typing import NamedTuple, Protocol, runtime_checkable

from tikdown_rs.core.tasks import create_supervised_task

from .events import EVENT_PROFILE_REFRESHED, EVENT_SELFCHECK_OK, render

logger = logging.getLogger(__name__)

# Events NOT worth a Telegram push: periodic internal confirmations. The
# catalog does not mark user-facing events, so this is an explicit skip set;
# everything else in EVENTS is sent by default.
DEFAULT_SKIPPED_EVENTS = frozenset({EVENT_PROFILE_REFRESHED, EVENT_SELFCHECK_OK})


@runtime_checkable
class NotificationService(Protocol):
    """The one channel contract: synchronous, best-effort, never raises."""

    def emit(self, event: str, payload: dict) -> None: ...


class NoopNotificationService:
    """Default service (6.2): the base scope emits nothing."""

    def emit(self, event: str, payload: dict) -> None:
        return None


class TelegramNotificationService:
    """Sends catalog events to one Telegram chat as fire-and-forget HTML messages.

    The emission channel stays SYNC (T-BACKFILL-15): ``emit`` renders the
    template and schedules the async send as a SUPERVISED background task
    (core/tasks.py: crash audit + bounded shutdown drain), so sync and async
    producers can both call it inline. Fire-and-forget semantics: delivery
    order is NOT guaranteed and a failed send is logged and dropped -- there
    is no offline spool (17.1 deferral). Shutdown never blocks on pending
    sends beyond the existing supervised-task drain. With ``bot=None`` it
    degrades to Noop behavior (debug log), safe before the bot is started.
    The rendered text is HTML-escaped (templates carry no tags; video titles
    and usernames are external data).
    """

    def __init__(
        self,
        bot: object | None,
        chat_id: int,
        *,
        skipped_events: frozenset[str] = DEFAULT_SKIPPED_EVENTS,
        renderer=render,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._skipped = frozenset(skipped_events)
        self._render = renderer

    def emit(self, event: str, payload: dict) -> None:
        if self._bot is None:
            logger.debug("notification %s dropped: bot not started (noop)", event)
            return
        if event in self._skipped:
            return
        try:
            text = html.escape(self._render(event, payload))
        except Exception as exc:  # noqa: BLE001 - never-raise contract (6.2)
            logger.warning("notification %s dropped: render failed (%r)", event, exc)
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("notification %s dropped: no running loop", event)
            return
        create_supervised_task(self._send(event, text), name=f"notify:{event}")

    async def _send(self, event: str, text: str) -> None:
        try:
            await self._bot.send_message(self._chat_id, text, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001 - best-effort: log and drop (17.1)
            # No spool (17.1): a failed send is logged and dropped.
            logger.warning("notification %s dropped: send failed (%r)", event, exc)


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

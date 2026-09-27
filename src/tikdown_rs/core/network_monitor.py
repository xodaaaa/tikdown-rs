"""Network online/offline state machine (8.1).

Trampas neutralizadas: T-ENGINE-7, T-BOT-14, T-BACKFILL-17. Regla: 8.1.

T-ENGINE-7: ``network_available`` is INJECTED by the caller and MUST be
created already SET: without a monitor the network is assumed available
(otherwise the first download hangs forever). This module never creates the
event and never clears it at construction.

T-BOT-14: ``network.online`` is emitted ONLY when the immediately previous
state was CONFIRMED offline (threshold reached); a blip (failures below the
threshold) emits nothing. The outage duration is captured BEFORE clearing
``offline_since``.

T-BACKFILL-17: a network failure never produces ``validation_state='invalid'``
nor consumes retries -- this module imports nothing from services/, so the
code path does not exist.

The probe hits a NEUTRAL endpoint (``settings.network_probe_url``, never
TikTok): any HTTP response below 500 means the network is up; connection
errors and timeouts are failures.
"""

import asyncio
import logging
import random
import time

import httpx

from tikdown_rs.core.backoff import backoff_seconds
from tikdown_rs.core.notifications.events import (
    EVENT_NETWORK_OFFLINE,
    EVENT_NETWORK_ONLINE,
)

logger = logging.getLogger("tikdown_rs.core.network_monitor")


async def _default_probe(url: str, timeout_seconds: float) -> bool:
    """HEAD probe with a hard timeout: response < 500 = reachable.

    Connection errors and timeouts are probe FAILURES (8.1), never exceptions
    that could kill the probe loop.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.head(url)
    except Exception:  # noqa: BLE001 - any transport error is 'offline'
        return False
    return response.status_code < 500


class NetworkMonitor:
    """Probe loop driving the 8.1 state machine over an INJECTED event."""

    def __init__(
        self,
        settings,
        network_available: asyncio.Event,
        probe_fn=None,
        clock_fn=time.monotonic,
        on_event=None,
        jitter_fn=random.uniform,
    ) -> None:
        # T-ENGINE-7: the caller creates and SETS this event; we only toggle it.
        self.network_available = network_available
        self._probe_fn = probe_fn if probe_fn is not None else _default_probe
        self._clock = clock_fn
        self._on_event = on_event
        self._jitter_fn = jitter_fn
        self._probe_url = settings.network_probe_url
        self._probe_timeout = settings.network_probe_timeout_seconds
        self._threshold = settings.network_offline_threshold_consecutive_failures
        self._consecutive_failures = 0
        self._offline = False
        self.offline_since: float | None = None

    @property
    def is_online(self) -> bool:
        """True while the last threshold evaluation says online (8.1)."""
        return not self._offline

    def _emit(self, event: str, **payload) -> None:
        if self._on_event is not None:
            self._on_event({"event": event, **payload})

    async def probe_once(self) -> bool:
        """One probe; updates the state machine. Returns the reachable verdict."""
        reachable = bool(await self._probe_fn(self._probe_url, self._probe_timeout))
        if reachable:
            if self._offline:
                # T-BOT-14: capture the outage duration BEFORE clearing.
                duration = self._clock() - (self.offline_since or self._clock())
                self._emit(EVENT_NETWORK_ONLINE, seconds=round(duration))
            self._consecutive_failures = 0
            self._offline = False
            self.offline_since = None
            self.network_available.set()
            return True
        self._consecutive_failures += 1
        if not self._offline and self._consecutive_failures >= self._threshold:
            self._offline = True
            self.offline_since = self._clock()  # captured AT the threshold crossing
            self.network_available.clear()
            self._emit(EVENT_NETWORK_OFFLINE, failures=self._consecutive_failures)
            logger.warning(
                "network offline: %s consecutive probe failures", self._consecutive_failures
            )
        return False

    async def _sleep(self, delay: float, stop_event: asyncio.Event) -> None:
        """Bounded wait: wakes early on stop, never sleeps past ``delay``."""
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except TimeoutError:
            pass

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """Probe loop: healthy cadence = base, then x2 up to the 120 s ceiling,
        +- jitter (8.1). Exits as soon as ``stop_event`` is set; every await
        is bounded."""
        while not stop_event.is_set():
            await self.probe_once()
            # 8.1: the backoff STARTS at 30 s after the FIRST failure
            # (failures 1 -> 30 s, 2 -> 60 s, 3+ -> 120 s ceiling); with zero
            # failures the base delay IS the healthy probe cadence.
            delay = backoff_seconds(
                max(self._consecutive_failures - 1, 0),
                jitter_fn=self._jitter_fn,
            )
            await self._sleep(delay, stop_event)

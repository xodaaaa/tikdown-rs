"""Polling supervision and strict PTB lifecycle sequences (plan §6.5, §6.1).

Trampas neutralizadas (Apéndice A): T-BOT-1, T-BOT-2.

- T-BOT-1: PTB >= 20 forbids ``run_polling()`` inside an existing event loop.
  The ONLY sanctioned lifecycle is the strict sequence here: startup
  ``initialize() -> start() -> updater.start_polling(timeout=25)`` and shutdown
  ``updater.stop() -> stop() -> shutdown()``. Each SHUTDOWN stage is
  exception-tolerant (logged and skipped past) so a failed stage never skips
  the remaining ones.
- T-BOT-2 + §6.5 PTB >= 20 caveat: ``getUpdates`` errors may not reach
  ``add_error_handler`` reliably, and the Bot API allows only ONE getUpdates
  session -- a manual diagnostic call kills live polling with a 409 Conflict.
  The robust detection path is a periodic ``getMe`` healthcheck (NEVER
  ``getUpdates``), run as a supervised background task: after
  ``polling_healthcheck_max_failures`` CONSECUTIVE failures the whole bot is
  restarted WITHOUT restarting the daemon; a failed restart is retried on the
  next cycle (the failure counter is NOT reset while the bot is down).
"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: §6.1: getUpdates polling timeout passed to ``updater.start_polling``.
POLLING_TIMEOUT_SECONDS = 25

#: Per-call cap for the getMe healthcheck: a hung getMe must count as ONE
#: cycle failure, not wedge the supervisor. min(interval, 10) bounds the wait
#: by the cycle interval itself, and never lets a single probe exceed 10 s.
GET_ME_TIMEOUT_CAP_SECONDS = 10.0


#: Per-stage deadline for the strict lifecycle sequences (R3-001/R3-003): a
#: hung stage await must RAISE instead of wedging daemon startup or shutdown
#: forever (an exception handler cannot rescue a non-returning await). Generous
#: bound: real PTB stages complete in seconds; a stuck stage is a wedge.
LIFECYCLE_STAGE_TIMEOUT_SECONDS = 30.0


async def _bounded(stage: str, awaitable: Any) -> None:
    """Run one lifecycle stage under a deadline (R3-001/R3-003).

    A timeout is re-raised as ``RuntimeError`` so callers can distinguish a
    wedged stage from a fast failure; ``wait_for`` cancels the hung await.
    """
    try:
        await asyncio.wait_for(awaitable, timeout=LIFECYCLE_STAGE_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise RuntimeError(
            f"bot lifecycle stage {stage} timed out after {LIFECYCLE_STAGE_TIMEOUT_SECONDS:.0f}s"
        ) from exc


async def start_bot_polling(app: Any) -> None:
    """Strict T-BOT-1 startup sequence; NEVER ``run_polling()`` on a live loop.

    Every stage is deadline-bounded (R3-003): a hung stage raises instead of
    blocking daemon startup forever (the bot is an optional enhancement).
    """
    await _bounded("initialize", app.initialize())
    await _bounded("start", app.start())
    await _bounded("start_polling", app.updater.start_polling(timeout=POLLING_TIMEOUT_SECONDS))


async def stop_bot_polling(app: Any) -> None:
    """Strict T-BOT-1 shutdown sequence; each stage is exception-tolerant AND
    deadline-bounded (R3-001).

    A failed stage is logged and the NEXT stage still runs (try-per-stage, not
    a fail-fast chain), and a hung stage is cancelled by its deadline, so a
    wedged updater can never skip ``app.shutdown()`` nor wedge shutdown.
    """
    updater = getattr(app, "updater", None)
    stages = (
        ("updater.stop", updater.stop if updater is not None else None),
        ("app.stop", app.stop),
        ("app.shutdown", app.shutdown),
    )
    for name, stage in stages:
        if stage is None:
            continue
        try:
            await _bounded(name, stage())
        except Exception:
            logger.exception("bot stop stage %s failed; continuing shutdown", name)


class PollingSupervisor:
    """§6.5 background loop: getMe healthcheck -> full bot restart on threshold.

    The loop is strictly sequential (sleep -> check -> maybe restart), so the
    ``_restarting`` flag only guards re-entrant/concurrent entries into the
    restart path. All failures are logged; the supervisor itself never raises
    (the daemon must not die because of the bot).
    """

    def __init__(
        self,
        app: Any,
        *,
        interval: int,
        max_failures: int,
        sleep_fn: Any = asyncio.sleep,
    ) -> None:
        self._app = app
        self._interval = interval
        self._max_failures = max_failures
        self._sleep_fn = sleep_fn
        self._restarting = False
        self._consecutive_failures = 0

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    async def run(self) -> None:
        """Supervised task body (§6.5): periodic healthcheck until cancelled."""
        while True:
            await self._sleep_fn(self._interval)
            await self._check_once()

    async def _check_once(self) -> None:
        """One healthcheck cycle: a getMe timeout/exception counts as ONE failure."""
        try:
            await asyncio.wait_for(
                self._app.bot.get_me(),
                timeout=min(self._interval, GET_ME_TIMEOUT_CAP_SECONDS),
            )
        except Exception as exc:  # noqa: BLE001 - ANY failure is one cycle failure (§6.5)
            self._consecutive_failures += 1
            logger.warning(
                "bot healthcheck failed (%d/%d consecutive): %r",
                self._consecutive_failures,
                self._max_failures,
                exc,
            )
            if self._consecutive_failures >= self._max_failures and not self._restarting:
                await self._restart()
        else:
            self._consecutive_failures = 0

    async def _restart(self) -> None:
        """Full bot restart WITHOUT restarting the daemon (§6.5, T-BOT-1 order)."""
        self._restarting = True
        try:
            logger.warning(
                "bot unhealthy after %d consecutive healthcheck failures; restarting bot",
                self._consecutive_failures,
            )
            await stop_bot_polling(self._app)
            await start_bot_polling(self._app)
            self._consecutive_failures = 0  # restart success resets the counter
            logger.info("bot restarted: polling is back up")
        except Exception:
            # Counter intentionally NOT reset: the next cycle retries the restart.
            logger.exception("bot restart failed; will retry on the next healthcheck cycle")
        finally:
            self._restarting = False

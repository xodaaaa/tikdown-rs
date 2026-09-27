"""Polling supervision tests (plan §6.5, task T7).

Deterministic: injected sleep (no real waits beyond millisecond-scale
wait_for timeouts), a stub Application whose bot.get_me behavior is scripted,
and the REAL start/stop sequences from bot.supervision executed against the
stub (no network, no real Application, §13.1). Covered: consecutive-failure
threshold, reset on success, restart call order (T-BOT-1), _restarting guard,
failed restart retried next cycle, hung get_me counts as a failure (T-BOT-2:
healthcheck is getMe, NEVER getUpdates), clean task cancellation.
"""

import asyncio
import contextlib

from tikdown_rs.bot.supervision import (
    GET_ME_TIMEOUT_CAP_SECONDS,
    POLLING_TIMEOUT_SECONDS,
    PollingSupervisor,
    start_bot_polling,
    stop_bot_polling,
)


class StubUpdater:
    def __init__(self, calls: list[tuple], slow_stop: float = 0.0) -> None:
        self._calls = calls
        self._slow_stop = slow_stop
        self.start_polling_kwargs: dict | None = None

    async def start_polling(self, **kwargs) -> None:
        self.start_polling_kwargs = kwargs
        self._calls.append(("updater.start_polling", kwargs))

    async def stop(self) -> None:
        if self._slow_stop:
            await asyncio.sleep(self._slow_stop)
        self._calls.append(("updater.stop", {}))


class StubBot:
    """Scripted get_me: outcomes consumed in order, the LAST one repeats."""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = outcomes
        self.calls = 0

    async def get_me(self):
        index = min(self.calls, len(self._outcomes) - 1)
        self.calls += 1
        outcome = self._outcomes[index]
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            await outcome()
        return outcome


class StubApplication:
    def __init__(self, get_me_outcomes: list | None = None, slow_stop: float = 0.0) -> None:
        self.calls: list[tuple] = []
        self.updater = StubUpdater(self.calls, slow_stop=slow_stop)
        self.bot = StubBot(get_me_outcomes if get_me_outcomes is not None else [{"id": 1}])

    async def initialize(self) -> None:
        self.calls.append(("initialize", {}))

    async def start(self) -> None:
        self.calls.append(("start", {}))

    async def stop(self) -> None:
        self.calls.append(("app.stop", {}))

    async def shutdown(self) -> None:
        self.calls.append(("app.shutdown", {}))


def _supervisor(app, *, interval=0.05, max_failures=3) -> PollingSupervisor:
    return PollingSupervisor(app, interval=interval, max_failures=max_failures)


def _restart_sequences(calls: list[tuple]) -> list[list[str]]:
    """Split the recorded call log into restart (stop+start) sequences."""
    names = [name for name, _ in calls]
    return [names[i : i + 6] for i in range(0, len(names), 6)]


# --- healthcheck cycle -------------------------------------------------------


async def test_failures_below_threshold_do_not_restart():
    app = StubApplication(get_me_outcomes=[RuntimeError("down")])
    supervisor = _supervisor(app, max_failures=3)
    for _ in range(2):
        await supervisor._check_once()
    assert app.calls == []  # no restart before max_failures


async def test_consecutive_failures_trigger_restart_and_counter_resets():
    app = StubApplication(get_me_outcomes=[RuntimeError("down")])
    supervisor = _supervisor(app, max_failures=3)
    for _ in range(3):
        await supervisor._check_once()
    assert len(_restart_sequences(app.calls)) == 1  # threshold reached -> one restart
    assert supervisor.consecutive_failures == 0  # restart success resets the counter
    for _ in range(2):
        await supervisor._check_once()
    assert len(_restart_sequences(app.calls)) == 1  # counter started from zero again


async def test_success_resets_consecutive_failure_counter():
    app = StubApplication(
        get_me_outcomes=[
            RuntimeError("down"),
            RuntimeError("down"),
            {"id": 1},
            RuntimeError("down"),
            RuntimeError("down"),
        ]
    )
    supervisor = _supervisor(app, max_failures=3)
    for _ in range(5):  # fail, fail, ok, fail, fail -> never reaches 3
        await supervisor._check_once()
    assert app.calls == []
    assert supervisor.consecutive_failures == 2


async def test_restart_call_order_is_strict_t_bot_1():
    app = StubApplication(get_me_outcomes=[RuntimeError("down")])
    supervisor = _supervisor(app, max_failures=1)
    await supervisor._check_once()
    assert [name for name, _ in app.calls] == [
        "updater.stop",
        "app.stop",
        "app.shutdown",
        "initialize",
        "start",
        "updater.start_polling",
    ]
    assert app.updater.start_polling_kwargs == {"timeout": POLLING_TIMEOUT_SECONDS}
    assert POLLING_TIMEOUT_SECONDS == 25  # §6.1: getUpdates timeout=25


async def test_restarting_flag_prevents_concurrent_restart():
    app = StubApplication(get_me_outcomes=[RuntimeError("down")], slow_stop=0.02)
    supervisor = _supervisor(app, max_failures=1)
    first = asyncio.create_task(supervisor._check_once())
    await asyncio.sleep(0.005)  # first check is inside its restart now
    assert supervisor.restarting  # flag is set while the restart runs
    await supervisor._check_once()  # concurrent entry must not restart again
    await first
    assert len(_restart_sequences(app.calls)) == 1
    assert not supervisor.restarting


async def test_failed_restart_is_retried_next_cycle_and_counter_not_reset():
    class FlakyInitializeApp(StubApplication):
        async def initialize(self):
            if self.bot.calls <= 2:  # first restart attempt only
                raise RuntimeError("restart init failed")
            self.calls.append(("initialize", {}))

    app = FlakyInitializeApp(get_me_outcomes=[RuntimeError("down")])
    supervisor = _supervisor(app, max_failures=2)
    await supervisor._check_once()
    await supervisor._check_once()  # threshold (2) -> restart attempt FAILS
    assert [name for name, _ in app.calls] == [
        "updater.stop",
        "app.stop",
        "app.shutdown",
        # start sequence raised inside initialize BEFORE recording: restart failed
    ]
    assert supervisor.consecutive_failures == 2  # counter NOT reset while bot is down
    await supervisor._check_once()  # next cycle retries the restart
    assert [name for name, _ in app.calls] == [
        "updater.stop",
        "app.stop",
        "app.shutdown",  # failed attempt (initialize raised unrecorded)
        "updater.stop",  # retry: full stop+start sequence
        "app.stop",
        "app.shutdown",
        "initialize",
        "start",
        "updater.start_polling",
    ]
    assert supervisor.consecutive_failures == 0  # retry succeeded -> counter reset


async def test_hung_get_me_counts_as_one_failure():
    async def hang():
        await asyncio.sleep(30)

    app = StubApplication(get_me_outcomes=[hang])
    supervisor = _supervisor(app, interval=0.05, max_failures=1)
    await supervisor._check_once()  # wait_for timeout = min(interval, cap)
    assert len(_restart_sequences(app.calls)) == 1
    assert GET_ME_TIMEOUT_CAP_SECONDS == 10


# --- run() task lifecycle ----------------------------------------------------


async def test_run_task_is_cancelled_cleanly():
    app = StubApplication(get_me_outcomes=[{"id": 1}])
    supervisor = _supervisor(app, interval=0.005)
    task = asyncio.create_task(supervisor.run())
    await asyncio.sleep(0.02)  # a few healthy cycles run
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled()


# --- strict sequences: per-stage exception tolerance (T-BOT-1) ---------------


async def test_start_sequence_is_initialize_start_start_polling():
    app = StubApplication()
    await start_bot_polling(app)
    assert [name for name, _ in app.calls] == [
        "initialize",
        "start",
        "updater.start_polling",
    ]
    assert app.updater.start_polling_kwargs == {"timeout": POLLING_TIMEOUT_SECONDS}


async def test_stop_sequence_tolerates_failed_stages_and_never_skips_rest():
    class BrokenStopApp(StubApplication):
        async def stop(self):
            self.calls.append(("app.stop", {}))
            raise RuntimeError("app.stop exploded")

    app = BrokenStopApp()
    await stop_bot_polling(app)  # must NOT raise
    assert [name for name, _ in app.calls] == [
        "updater.stop",
        "app.stop",
        "app.shutdown",
    ]


async def test_stop_sequence_without_updater_still_shuts_down():
    app = StubApplication()
    app.updater = None  # start_polling never ran
    await stop_bot_polling(app)
    assert [name for name, _ in app.calls] == ["app.stop", "app.shutdown"]


# --- T-BOT-2 guard: the module must never touch getUpdates -------------------


def test_supervision_never_calls_get_updates():
    import inspect

    from tikdown_rs.bot import supervision

    source = inspect.getsource(supervision)
    assert "get_updates" not in source
    assert "get_me" in source  # healthcheck is getMe (T-BOT-2)

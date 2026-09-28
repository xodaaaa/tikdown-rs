"""Daemon <-> bot wiring tests (plan §6.1, task T6 / §5.1 step 8).

Deterministic: tmp_path DATA_DIR, real file DB, stubbed TikDownBot class
(no real Application, no token use, no network, §13.1). Covered: bot starts
ONLY with TELEGRAM_BOT_TOKEN, dependencies injected (T-BOT-3: the daemon's own
session_factory, ONE engine), strict initialize/start/start_polling sequence
(T-BOT-1), supervised-task registration, strict stop order on daemon shutdown,
and graceful degradation when the bot fails to start (the daemon stays alive).
"""

import asyncio
from typing import ClassVar

import pytest

import tikdown_rs.daemon.run as daemon_run
from tikdown_rs.core.config import load_settings
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.notifications import InMemoryNotificationService
from tikdown_rs.core.tasks import supervised_tasks
from tikdown_rs.daemon.run import shutdown_daemon, start_daemon


def _fake_which(missing: set[str] = frozenset()):
    def fake(name):
        return None if name in missing else f"/fake-bin/{name}"

    return fake


async def _seed_valid_cookie(data_dir) -> None:
    from tikdown_rs.models import Cookie

    engine = create_db_engine(sqlite_url_for(data_dir))
    try:
        async with make_session_factory(engine)() as session:
            session.add(Cookie(label="c", cookie_blob=b"netscape", validation_state="valid"))
            await session.commit()
    finally:
        await engine.dispose()


class StubUpdater:
    def __init__(self, calls: list[tuple]) -> None:
        self._calls = calls

    async def start_polling(self, **kwargs) -> None:
        self._calls.append(("updater.start_polling", kwargs))

    async def stop(self) -> None:
        self._calls.append(("updater.stop", {}))


class StubBot:
    def __init__(self) -> None:
        self.calls = 0

    async def get_me(self):
        self.calls += 1
        return {"id": 1, "username": "stub"}


class StubApplication:
    def __init__(self, fail_initialize: bool = False) -> None:
        self.calls: list[tuple] = []
        self._fail_initialize = fail_initialize
        self.updater = StubUpdater(self.calls)
        self.bot = StubBot()

    async def initialize(self) -> None:
        if self._fail_initialize:
            raise RuntimeError("initialize failed")
        self.calls.append(("initialize", {}))

    async def start(self) -> None:
        self.calls.append(("start", {}))

    async def stop(self) -> None:
        self.calls.append(("app.stop", {}))

    async def shutdown(self) -> None:
        self.calls.append(("app.shutdown", {}))

    def add_handler(self, handler) -> None:
        self.calls.append(("add_handler", handler))


class StubTikDownBot:
    """Test double replacing daemon_run.TikDownBot (constructor = sync wiring)."""

    instances: ClassVar[list["StubTikDownBot"]] = []

    def __init__(self, settings, session_factory) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.handlers_registered = False
        self.application = StubApplication()
        StubTikDownBot.instances.append(self)

    def register_handlers(self) -> None:
        self.handlers_registered = True


class FailingStubTikDownBot(StubTikDownBot):
    def __init__(self, settings, session_factory) -> None:
        super().__init__(settings, session_factory)
        self.application = StubApplication(fail_initialize=True)


async def _start_settings(tmp_path, monkeypatch, *, token: bool):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    if token:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:stub-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    return load_settings()


@pytest.mark.timeout(30)
async def test_daemon_starts_bot_when_token_is_set(tmp_path, monkeypatch) -> None:
    """§5.1 step 8 + §6.1: injected deps, strict start sequence, supervised task."""
    monkeypatch.setattr(daemon_run, "TikDownBot", StubTikDownBot)
    StubTikDownBot.instances.clear()
    settings = await _start_settings(tmp_path, monkeypatch, token=True)
    notifications = InMemoryNotificationService()

    runtime = await start_daemon(
        settings,
        notifications=notifications,
        impersonation_fn=lambda: (True, "private-api", 3),
        which_fn=_fake_which(),
    )
    try:
        assert len(StubTikDownBot.instances) == 1
        bot = StubTikDownBot.instances[0]
        # T-BOT-3: the daemon's OWN session_factory is injected (ONE engine).
        assert bot.session_factory is runtime.components.session_factory
        assert bot.settings is settings
        assert bot.handlers_registered
        names = [name for name, _ in bot.application.calls]
        assert names[:3] == ["initialize", "start", "updater.start_polling"]  # T-BOT-1
        assert bot.application.calls[2] == ("updater.start_polling", {"timeout": 25})
        assert runtime.components.bot is bot
        # §5.5: the supervision loop runs as a supervised task (drain covers it).
        assert any(t.get_name() == "bot-polling-supervision" for t in supervised_tasks())
    finally:
        await shutdown_daemon(runtime)
    # T-BOT-1 shutdown order, after the start sequence.
    stop_names = [name for name, _ in StubTikDownBot.instances[0].application.calls]
    assert stop_names[-3:] == ["updater.stop", "app.stop", "app.shutdown"]


@pytest.mark.timeout(30)
async def test_daemon_without_token_never_builds_a_bot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(daemon_run, "TikDownBot", StubTikDownBot)
    StubTikDownBot.instances.clear()
    settings = await _start_settings(tmp_path, monkeypatch, token=False)
    runtime = await start_daemon(
        settings,
        impersonation_fn=lambda: (True, "private-api", 3),
        which_fn=_fake_which(),
    )
    try:
        assert StubTikDownBot.instances == []
        assert runtime.components.bot is None
        assert not any(t.get_name() == "bot-polling-supervision" for t in supervised_tasks())
    finally:
        await shutdown_daemon(runtime)


@pytest.mark.timeout(30)
async def test_bot_startup_failure_degrades_daemon_gracefully(tmp_path, monkeypatch) -> None:
    """§5.1 step 8: the bot is an enhancement -- a failed start NEVER kills the daemon."""
    monkeypatch.setattr(daemon_run, "TikDownBot", FailingStubTikDownBot)
    settings = await _start_settings(tmp_path, monkeypatch, token=True)
    runtime = await start_daemon(
        settings,
        impersonation_fn=lambda: (True, "private-api", 3),
        which_fn=_fake_which(),
    )
    try:
        assert runtime.components.bot is None  # degraded, not dead
        assert len(runtime.scheduler.get_jobs()) == 7  # scheduler still registered
    finally:
        await shutdown_daemon(runtime)  # must not raise on the absent bot


@pytest.mark.timeout(30)
async def test_supervisor_is_wired_to_the_bot_app_and_settings(tmp_path, monkeypatch) -> None:
    """§6.5 wiring: the supervised loop gets THE wired app and the Settings knobs.

    The real getMe loop behavior (thresholds, reset, restart order) is covered
    deterministically in test_bot_supervision.py; here only the daemon wiring
    is asserted -- without real sleeps (the loop's first beat is one interval
    away, 30 s by default).
    """
    recorded: dict[str, object] = {}

    class SpyPollingSupervisor:
        def __init__(self, app, *, interval, max_failures) -> None:
            recorded["app"] = app
            recorded["interval"] = interval
            recorded["max_failures"] = max_failures

        async def run(self) -> None:
            await asyncio.Event().wait()  # idle; drain cancels it at shutdown

    monkeypatch.setattr(daemon_run, "TikDownBot", StubTikDownBot)
    monkeypatch.setattr(daemon_run, "PollingSupervisor", SpyPollingSupervisor)
    StubTikDownBot.instances.clear()
    monkeypatch.setenv("POLLING_HEALTHCHECK_INTERVAL", "7")
    monkeypatch.setenv("POLLING_HEALTHCHECK_MAX_FAILURES", "2")
    settings = await _start_settings(tmp_path, monkeypatch, token=True)
    runtime = await start_daemon(
        settings,
        impersonation_fn=lambda: (True, "private-api", 3),
        which_fn=_fake_which(),
    )
    try:
        assert any(t.get_name() == "bot-polling-supervision" for t in supervised_tasks())
        assert recorded["app"] is StubTikDownBot.instances[0].application
        assert recorded["interval"] == settings.polling_healthcheck_interval == 7
        assert recorded["max_failures"] == settings.polling_healthcheck_max_failures == 2
    finally:
        await shutdown_daemon(runtime)


async def test_start_bot_failure_is_atomic_and_stops_partial_app(monkeypatch):
    """R3-002: a failure after a partial start stops the app before re-raising.

    Without the fix, components.bot stays None (the assignment only happens on
    a successful return) and the partially started bot would never be shut
    down. The fix makes _start_bot stop the partial app and re-raise, so the
    daemon's log-and-continue keeps a clean, leak-free state.
    """
    from types import SimpleNamespace

    class FailPollingUpdater:
        def __init__(self, calls: list[tuple]) -> None:
            self._calls = calls

        async def start_polling(self, **kwargs) -> None:
            self._calls.append(("updater.start_polling", kwargs))
            raise RuntimeError("start_polling exploded")

        async def stop(self) -> None:
            self._calls.append(("updater.stop", {}))

    class FailPollingApp(StubApplication):
        def __init__(self) -> None:
            super().__init__()
            self.updater = FailPollingUpdater(self.calls)

    class AtomicStubBot(StubTikDownBot):
        def __init__(self, settings, session_factory) -> None:
            super().__init__(settings, session_factory)
            self.application = FailPollingApp()

    monkeypatch.setattr(daemon_run, "TikDownBot", AtomicStubBot)
    components = SimpleNamespace(settings=object(), session_factory=object())
    with pytest.raises(RuntimeError, match="start_polling exploded"):
        await daemon_run._start_bot(components)
    bot = AtomicStubBot.instances[-1]
    assert [name for name, _ in bot.application.calls] == [
        "initialize",
        "start",
        "updater.start_polling",
        "updater.stop",
        "app.stop",
        "app.shutdown",
    ]

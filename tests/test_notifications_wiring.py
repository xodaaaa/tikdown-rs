"""Wiring of the notifications layer to the Telegram bot (Chain 2).

Covers: TelegramNotificationService (render + fire-and-forget send), the
_no-running-loop and send-failure paths, and the daemon run.py helper that
replaces the default Noop once the bot is live.
"""

import asyncio
import logging
import time
from types import SimpleNamespace

from tikdown_rs.core.notifications import (
    EVENT_DAEMON_STARTED,
    EVENT_PROFILE_REFRESHED,
    NoopNotificationService,
    TelegramNotificationService,
)
from tikdown_rs.daemon.run import _DEFAULT_NOTIFICATIONS, _notify_service_for


class _FakeBot:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[int, str, dict]] = []
        self._fail = fail

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        if self._fail:
            raise RuntimeError("boom")
        self.calls.append((chat_id, text, kwargs))


async def _wait_until(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert condition(), "condition not reached in time"


async def test_emit_renders_and_sends_html_to_chat_id():
    bot = _FakeBot()
    service = TelegramNotificationService(bot, 42)
    service.emit(EVENT_DAEMON_STARTED, {"pid": 1, "data_dir": "/tmp/d"})
    await _wait_until(lambda: len(bot.calls) == 1)
    chat_id, text, kwargs = bot.calls[0]
    assert chat_id == 42
    assert "Daemon started (pid 1, data_dir /tmp/d)." in text
    assert kwargs.get("parse_mode") == "HTML"


async def test_emit_send_failure_logs_warning_and_does_not_raise(caplog):
    bot = _FakeBot(fail=True)
    service = TelegramNotificationService(bot, 42)
    with caplog.at_level(logging.WARNING):
        service.emit(EVENT_DAEMON_STARTED, {"pid": 1})  # must not raise
        await asyncio.sleep(0.05)
    assert any("send failed" in record.message for record in caplog.records)


def test_emit_without_running_loop_logs_and_does_not_crash(caplog):
    service = TelegramNotificationService(_FakeBot(), 42)
    with caplog.at_level(logging.DEBUG):
        service.emit(EVENT_DAEMON_STARTED, {"pid": 1})  # no loop: no crash
    assert any("no running loop" in record.message for record in caplog.records)


async def test_skipped_periodic_events_are_not_sent():
    bot = _FakeBot()
    service = TelegramNotificationService(bot, 42)
    service.emit(EVENT_PROFILE_REFRESHED, {"username": "someone"})
    await asyncio.sleep(0.05)
    assert bot.calls == []


def test_notify_service_for_replaces_default_noop_when_bot_is_live():
    components = SimpleNamespace(
        notifications=_DEFAULT_NOTIFICATIONS,
        settings=SimpleNamespace(telegram_chat_id="42"),
    )
    bot = SimpleNamespace(application=SimpleNamespace(bot=_FakeBot()))
    service = _notify_service_for(components, bot)
    assert isinstance(service, TelegramNotificationService)
    assert service._chat_id == 42


def test_notify_service_for_keeps_injected_service():
    injected = NoopNotificationService()  # a NON-default instance: never replaced
    components = SimpleNamespace(
        notifications=injected,
        settings=SimpleNamespace(telegram_chat_id="42"),
    )
    bot = SimpleNamespace(application=SimpleNamespace(bot=_FakeBot()))
    assert _notify_service_for(components, bot) is injected


def test_notify_service_for_noop_without_bot_or_chat_id():
    components = SimpleNamespace(
        notifications=NoopNotificationService(),
        settings=SimpleNamespace(telegram_chat_id=""),
    )
    assert isinstance(_notify_service_for(components, None), NoopNotificationService)
    bot = SimpleNamespace(application=SimpleNamespace(bot=_FakeBot()))
    assert isinstance(_notify_service_for(components, bot), NoopNotificationService)

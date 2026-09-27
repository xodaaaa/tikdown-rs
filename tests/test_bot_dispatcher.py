"""Unit + smoke tests for the bot dispatcher (plan §6.2/§6.3, task T5a).

Pure parts are tested hard: ``build_caller`` (T-BOT-11/18), formatting helpers,
keyboard conversion, and the T-BOT-7 reply degradation via a stub bot. The
PTB-bound wiring is smoke-tested with stubbed ApplicationBuilder/service
doubles: no real Application, no token use, no network (§13.1).
"""

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar, Self

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from tikdown_rs.bot import dispatcher
from tikdown_rs.bot.dispatcher import (
    MSG_CALLBACK_EXPIRED,
    MSG_INTERNAL_ERROR,
    MSG_LAST_PENDING,
    MSG_NO_DAEMON_STATE,
    MSG_STATS_PENDING,
    MSG_THROTTLED,
    MSG_UNAUTHORIZED,
    TikDownBot,
    build_caller,
    format_disk_message,
    format_status_message,
    heartbeat_age_seconds,
    parse_allowed_user_ids,
    parse_chat_id,
    to_inline_keyboard,
)
from tikdown_rs.bot.security import SecurityGuard
from tikdown_rs.core.config import Settings

# --- stubs and fixtures ------------------------------------------------------


class StubApplicationBuilder:
    """Records the builder chain; ``build()`` returns a stub Application."""

    instances: ClassVar[list["StubApplicationBuilder"]] = []

    def __init__(self) -> None:
        self.token_arg: str | None = None
        self.rate_limiter_arg: object = None
        self.built: StubApplication | None = None
        StubApplicationBuilder.instances.append(self)

    def token(self, value: str) -> "StubApplicationBuilder":
        self.token_arg = value
        return self

    def rate_limiter(self, limiter: object) -> "StubApplicationBuilder":
        self.rate_limiter_arg = limiter
        return self

    def build(self) -> "StubApplication":
        self.built = StubApplication()
        return self.built


class StubAIORateLimiter:
    instances: ClassVar[list["StubAIORateLimiter"]] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        StubAIORateLimiter.instances.append(self)


class StubApplication:
    def __init__(self) -> None:
        self.handlers: list[object] = []

    def add_handler(self, handler: object) -> None:
        self.handlers.append(handler)


@dataclass
class StubChat:
    id: int = 111
    sent: list[dict] = field(default_factory=list)
    fail_first_html: str | None = None

    async def send_message(self, text: str, **kwargs) -> None:
        if self.fail_first_html and kwargs.get("parse_mode") == "HTML":
            message, self.fail_first_html = self.fail_first_html, None
            raise BadRequest(message)
        self.sent.append({"text": text, **kwargs})


@dataclass
class StubUser:
    id: int = 222


@dataclass
class StubQuery:
    data: str | None = None
    answers: list[str | None] = field(default_factory=list)
    edits: list[dict] = field(default_factory=list)

    async def answer(self, text: str | None = None, **kwargs) -> None:
        self.answers.append(text)

    async def edit_message_text(self, text: str, **kwargs) -> None:
        self.edits.append({"text": text, **kwargs})


@dataclass
class StubUpdate:
    effective_chat: StubChat | None
    effective_user: StubUser | None = None
    callback_query: StubQuery | None = None


class StubSessionFactory:
    """Async context manager standing in for a session; records entries."""

    def __init__(self) -> None:
        self.entries = 0

    def __call__(self) -> "StubSessionFactory":
        self.entries += 1
        return self

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False


def make_settings(**overrides) -> Settings:
    values = {
        "telegram_bot_token": "TEST:TOKEN",
        "telegram_chat_id": "111",
        "telegram_user_id": "",
    }
    values.update(overrides)
    return Settings(**values)


def make_clock(start: float = 1000.0):
    """Controllable monotonic clock shared with an injected SecurityGuard."""
    state = {"now": start}
    return state, lambda: state["now"]


@pytest.fixture()
def bot_factory(monkeypatch):
    """Build a TikDownBot with the PTB builder stubbed out."""

    def factory(settings=None, session_factory=None, guard=None):
        StubApplicationBuilder.instances.clear()
        StubAIORateLimiter.instances.clear()
        monkeypatch.setattr(dispatcher, "ApplicationBuilder", StubApplicationBuilder)
        monkeypatch.setattr(dispatcher, "AIORateLimiter", StubAIORateLimiter)
        session_factory = session_factory or StubSessionFactory()
        bot = TikDownBot(
            settings or make_settings(),
            session_factory,
            guard=guard,
        )
        return bot, session_factory

    return factory


# --- pure helpers ------------------------------------------------------------


class TestBuildCaller:
    def test_with_chat_and_user(self):
        update = StubUpdate(effective_chat=StubChat(id=7), effective_user=StubUser(id=9))
        caller = build_caller(update)
        assert caller.chat_id == 7
        assert caller.user_id == 9
        assert caller.has_chat is True

    def test_without_chat_is_tolerated(self):
        # T-BOT-11/18: effective_chat None must not raise and must flag has_chat=False.
        update = StubUpdate(effective_chat=None, effective_user=StubUser(id=9))
        caller = build_caller(update)
        assert caller.chat_id is None
        assert caller.user_id == 9
        assert caller.has_chat is False


class TestSettingsParsing:
    def test_parse_chat_id(self):
        assert parse_chat_id(" -42 ") == -42
        assert parse_chat_id("") is None
        assert parse_chat_id("not-a-number") is None

    def test_parse_allowed_user_ids(self):
        assert parse_allowed_user_ids("1, 2,3") == (1, 2, 3)
        assert parse_allowed_user_ids("") is None
        assert parse_allowed_user_ids("  ") is None
        assert parse_allowed_user_ids("1,x,2") == (1, 2)


class TestFormatDiskMessage:
    def test_lines(self):
        text = format_disk_message(23.45, 10, True)
        assert "23.4%" in text or "23.5%" in text
        assert "10%" in text
        assert "sí" in text

    def test_not_paused(self):
        assert "no" in format_disk_message(90.0, 10, False)


class TestHeartbeatAge:
    def test_none_when_missing(self):
        assert heartbeat_age_seconds(None, datetime.now(UTC)) is None

    def test_age_seconds(self):
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert heartbeat_age_seconds("2025-01-01T11:59:30+00:00", now) == 30.0

    def test_naive_timestamp_treated_as_utc(self):
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert heartbeat_age_seconds("2025-01-01T11:59:00", now) == 60.0


class TestFormatStatusMessage:
    def make_row(self, **overrides):
        row = SimpleNamespace(
            last_heartbeat_at="2025-01-01T11:59:50+00:00",
            monitor_running=True,
            degraded_reason=None,
        )
        for key, value in overrides.items():
            setattr(row, key, value)
        return row

    def test_lines(self):
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        text = format_status_message(
            self.make_row(),
            {"valid": 2, "invalid": 1, "inconclusive": 0},
            ["Errores recientes: 1"],
            now=now,
        )
        assert "10s" in text
        assert "activo" in text
        assert "2" in text and "1" in text
        assert "Errores recientes: 1" in text

    def test_unknown_heartbeat_and_no_errors(self):
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        text = format_status_message(
            self.make_row(last_heartbeat_at=None),
            {},
            [],
            now=now,
        )
        assert "desconocido" in text
        assert "Sin errores recientes" in text

    def test_degraded_reason_is_escaped(self):
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        text = format_status_message(
            self.make_row(degraded_reason="<b>oops & co</b>"),
            {},
            [],
            now=now,
        )
        assert "&lt;b&gt;oops &amp; co&lt;/b&gt;" in text
        assert "<b>oops" not in text


class TestToInlineKeyboard:
    def test_converts_plain_rows_to_markup(self):
        rows = [[{"text": "◀️", "callback_data": "listp:1:0"}]]
        markup = to_inline_keyboard(rows)
        assert isinstance(markup, InlineKeyboardMarkup)
        button = markup.inline_keyboard[0][0]
        assert isinstance(button, InlineKeyboardButton)
        assert button.text == "◀️"
        assert button.callback_data == "listp:1:0"

    def test_empty_rows(self):
        markup = to_inline_keyboard([])
        assert list(markup.inline_keyboard) == []


# --- construction and wiring smoke ------------------------------------------


class TestConstruction:
    def test_builder_chain_token_and_rate_limiter(self, bot_factory):
        bot, _ = bot_factory()
        builder = StubApplicationBuilder.instances[0]
        assert builder.token_arg == "TEST:TOKEN"
        assert builder.rate_limiter_arg is StubAIORateLimiter.instances[0]
        assert StubAIORateLimiter.instances[0].kwargs == {"max_retries": 3}
        assert bot.application is builder.built

    def test_guard_uses_settings_chat_and_allowlist(self, bot_factory):
        bot, _ = bot_factory(make_settings(telegram_user_id="222"))
        assert bot._guard._chat_id == 111
        assert bot._guard._allowed_user_ids == (222,)


class TestRegisterHandlers:
    def test_registers_all_commands_and_callback_once(self, bot_factory):
        bot, _ = bot_factory()
        bot.register_handlers()
        app = bot.application
        commands = [h.commands for h in app.handlers if hasattr(h, "commands")]
        assert sorted(c for group in commands for c in group) == [
            "disk",
            "help",
            "last",
            "list",
            "start",
            "stats",
            "status",
        ]
        callbacks = [h for h in app.handlers if hasattr(h, "pattern")]
        assert len(callbacks) == 1
        assert callbacks[0].pattern.pattern == r"^listp:"

    def test_idempotent(self, bot_factory):
        # T-BOT-4: double registration adds nothing.
        bot, _ = bot_factory()
        bot.register_handlers()
        count = len(bot.application.handlers)
        bot.register_handlers()
        assert len(bot.application.handlers) == count


# --- reply degradation (T-BOT-7) ---------------------------------------------


class TestReplyDegradation:
    async def test_parse_entities_failure_retries_plain_once(self, bot_factory):
        bot, _ = bot_factory()
        chat = StubChat(id=111)
        chat.fail_first_html = "Can't parse entities"
        update = StubUpdate(effective_chat=chat)
        await bot._reply(update, "hello <b>world</b>")
        # The failed HTML attempt raises before recording; the plain retry lands once.
        assert len(chat.sent) == 1
        assert "parse_mode" not in chat.sent[0]
        assert chat.sent[0]["text"] == "hello <b>world</b>"

    async def test_other_bad_request_propagates(self, bot_factory):
        bot, _ = bot_factory()
        chat = StubChat(id=111)

        async def send(text, **kwargs):
            raise BadRequest("Chat not found")

        chat.send_message = send
        update = StubUpdate(effective_chat=chat)
        with pytest.raises(BadRequest):
            await bot._reply(update, "hello")

    async def test_long_text_is_clipped(self, bot_factory):
        bot, _ = bot_factory()
        chat = StubChat(id=111)
        await bot._reply(StubUpdate(effective_chat=chat), "x" * 5000)
        assert len(chat.sent[0]["text"]) <= 4096

    async def test_no_chat_is_silent(self, bot_factory):
        bot, _ = bot_factory()
        await bot._reply(StubUpdate(effective_chat=None), "hello")  # must not raise


# --- command funnel: authz, throttle, burst ----------------------------------


class TestCommandFunnel:
    async def test_denied_reply_and_no_service_call(self, bot_factory):
        # Guard chat is 111; the update comes from another chat.
        bot, session_factory = bot_factory(make_settings(), StubSessionFactory())
        chat = StubChat(id=999)
        await bot.cmd_list(StubUpdate(effective_chat=chat), None)
        assert chat.sent and MSG_UNAUTHORIZED in chat.sent[0]["text"]
        assert session_factory.entries == 0

    async def test_throttled_second_command(self, bot_factory):
        clock, tick = make_clock()
        bot, _ = bot_factory(guard=SecurityGuard(111, clock=tick))
        chat = StubChat(id=111)
        update = StubUpdate(effective_chat=chat, effective_user=StubUser(id=222))
        await bot.cmd_stats(update, None)
        clock["now"] += 0.5
        await bot.cmd_stats(update, None)
        assert MSG_THROTTLED in chat.sent[1]["text"]

    async def test_burst_after_repeated_unauthorized(self, bot_factory):
        _clock, tick = make_clock()
        bot, _ = bot_factory(guard=SecurityGuard(111, clock=tick))
        chat = StubChat(id=999)
        for _ in range(5):
            await bot.cmd_help(StubUpdate(effective_chat=chat), None)
        assert str(5) in chat.sent[-1]["text"]

    async def test_service_error_becomes_generic_reply(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()

        async def boom(factory):
            raise RuntimeError("db exploded")

        monkeypatch.setattr(dispatcher, "list_accounts", boom)
        chat = StubChat(id=111)
        await bot.cmd_list(StubUpdate(effective_chat=chat), None)
        assert MSG_INTERNAL_ERROR in chat.sent[0]["text"]


# --- commands ----------------------------------------------------------------


class TestCommands:
    async def test_start_and_help_are_static(self, bot_factory):
        clock, tick = make_clock()
        bot, _ = bot_factory(guard=SecurityGuard(111, clock=tick))
        chat = StubChat(id=111)
        update = StubUpdate(effective_chat=chat, effective_user=StubUser(id=222))
        await bot.cmd_start(update, None)
        clock["now"] += 3
        await bot.cmd_help(update, None)
        assert chat.sent[0]["parse_mode"] == "HTML"
        assert "/list" in chat.sent[0]["text"]
        assert "/help" in chat.sent[1]["text"]

    async def test_stats_and_last_report_pending(self, bot_factory):
        clock, tick = make_clock()
        bot, _ = bot_factory(guard=SecurityGuard(111, clock=tick))
        chat = StubChat(id=111)
        update = StubUpdate(effective_chat=chat, effective_user=StubUser(id=222))
        await bot.cmd_stats(update, None)
        clock["now"] += 3
        await bot.cmd_last(update, None)
        assert MSG_STATS_PENDING in chat.sent[0]["text"]
        assert MSG_LAST_PENDING in chat.sent[1]["text"]

    async def test_list_renders_with_keyboard(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()
        rows = [{"username": f"user{i}", "backfill_status": "idle"} for i in range(7)]

        async def fake_list(factory):
            return rows

        monkeypatch.setattr(dispatcher, "list_accounts", fake_list)
        chat = StubChat(id=111)
        await bot.cmd_list(StubUpdate(effective_chat=chat), None)
        sent = chat.sent[0]
        assert sent["parse_mode"] == "HTML"
        assert "user0" in sent["text"]
        markup = sent["reply_markup"]
        assert isinstance(markup, InlineKeyboardMarkup)
        assert markup.inline_keyboard[0][0].callback_data.startswith("listp:")

    async def test_list_empty_no_keyboard(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()

        async def fake_list(factory):
            return []

        monkeypatch.setattr(dispatcher, "list_accounts", fake_list)
        chat = StubChat(id=111)
        await bot.cmd_list(StubUpdate(effective_chat=chat), None)
        assert chat.sent[0]["reply_markup"] is None
        assert "No hay cuentas" in chat.sent[0]["text"]

    async def test_disk(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()
        bot._settings = make_settings(disk_warning_free_percent=15)

        async def fake_read_status(session):
            return SimpleNamespace(downloads_paused=True)

        monkeypatch.setattr(dispatcher, "read_status", fake_read_status)
        monkeypatch.setattr(dispatcher.disk_probe, "free_percent", lambda path: 42.0)
        chat = StubChat(id=111)
        await bot.cmd_disk(StubUpdate(effective_chat=chat), None)
        text = chat.sent[0]["text"]
        assert "42.0%" in text
        assert "15%" in text
        assert "sí" in text

    async def test_disk_without_state(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()

        async def fake_read_status(session):
            return None

        monkeypatch.setattr(dispatcher, "read_status", fake_read_status)
        chat = StubChat(id=111)
        await bot.cmd_disk(StubUpdate(effective_chat=chat), None)
        assert MSG_NO_DAEMON_STATE in chat.sent[0]["text"]

    async def test_status(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()

        async def fake_read_status(session):
            return SimpleNamespace(
                last_heartbeat_at="2025-01-01T11:59:50+00:00",
                monitor_running=False,
                degraded_reason=None,
            )

        async def fake_cookie_counts(session):
            return {"valid": 1}

        async def fake_recent(session):
            return ["Errores recientes: 1"]

        monkeypatch.setattr(dispatcher, "read_status", fake_read_status)
        monkeypatch.setattr(dispatcher, "cookie_counts", fake_cookie_counts)
        monkeypatch.setattr(dispatcher, "recent_error_lines", fake_recent)
        chat = StubChat(id=111)
        await bot.cmd_status(StubUpdate(effective_chat=chat), None)
        text = chat.sent[0]["text"]
        assert "Latido" in text
        assert "Errores recientes: 1" in text


# --- callback: listp: pagination ---------------------------------------------


class TestListPageCallback:
    def make_query(self, page: int, ts: int | None = None) -> StubQuery:
        ts = int(time.time()) if ts is None else ts
        return StubQuery(data=f"listp:{ts}:{page}")

    async def test_success_edits_message(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()
        rows = [{"username": f"user{i}", "backfill_status": "idle"} for i in range(7)]

        async def fake_list(factory):
            return rows

        monkeypatch.setattr(dispatcher, "list_accounts", fake_list)
        query = self.make_query(1)
        update = StubUpdate(
            effective_chat=StubChat(id=111),
            effective_user=StubUser(id=222),
            callback_query=query,
        )
        await bot.on_list_page(update, None)
        assert query.answers[-1] is None
        assert len(query.edits) == 1
        assert query.edits[0]["parse_mode"] == "HTML"
        assert isinstance(query.edits[0]["reply_markup"], InlineKeyboardMarkup)

    async def test_always_answers_even_on_error(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()

        async def boom(factory):
            raise RuntimeError("db exploded")

        monkeypatch.setattr(dispatcher, "list_accounts", boom)
        query = self.make_query(0)
        update = StubUpdate(
            effective_chat=StubChat(id=111),
            effective_user=StubUser(id=222),
            callback_query=query,
        )
        await bot.on_list_page(update, None)
        assert len(query.answers) == 1

    async def test_expired_answers_without_edit(self, bot_factory):
        bot, _ = bot_factory()
        query = self.make_query(1, ts=int(time.time()) - 61)
        update = StubUpdate(
            effective_chat=StubChat(id=111),
            effective_user=StubUser(id=222),
            callback_query=query,
        )
        await bot.on_list_page(update, None)
        assert query.answers == [MSG_CALLBACK_EXPIRED]
        assert query.edits == []

    async def test_garbage_data_answers_expired(self, bot_factory):
        bot, _ = bot_factory()
        query = StubQuery(data="listp:zzz")
        update = StubUpdate(
            effective_chat=StubChat(id=111),
            effective_user=StubUser(id=222),
            callback_query=query,
        )
        await bot.on_list_page(update, None)
        assert query.answers == [MSG_CALLBACK_EXPIRED]
        assert query.edits == []

    async def test_unauthorized_answers_and_edits_denial(self, bot_factory):
        bot, _ = bot_factory()
        query = self.make_query(0)
        update = StubUpdate(
            effective_chat=StubChat(id=999),
            callback_query=query,
        )
        await bot.on_list_page(update, None)
        assert len(query.answers) == 1
        assert MSG_UNAUTHORIZED in query.edits[0]["text"]

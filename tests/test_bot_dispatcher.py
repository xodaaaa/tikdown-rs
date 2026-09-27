"""Unit + smoke tests for the bot dispatcher (plan §6.2/§6.3, tasks T5a + T5b).

Pure parts are tested hard: ``build_caller`` (T-BOT-11/18), formatting helpers,
keyboard conversion, the T-BOT-7 reply degradation via a stub bot, the mutating
commands' orchestration (service doubles asserting WHICH service was called with
WHICH args, T-BOT-4 idempotency), and the cookie-upload funnel (metadata size
gate BEFORE download, T-COOKIES-7 tempfile lifecycle). The PTB-bound wiring is
smoke-tested with stubbed ApplicationBuilder/service doubles: no real
Application, no token use, no network (§13.1).
"""

import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar, Self

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import MessageHandler, filters

from tikdown_rs.bot import dispatcher
from tikdown_rs.bot.dispatcher import (
    COOKIE_MAX_UPLOAD_BYTES,
    MSG_CALLBACK_EXPIRED,
    MSG_CHECK_PENDING,
    MSG_COOKIE_INSTRUCTIONS,
    MSG_COOKIE_TOO_LARGE,
    MSG_INTERNAL_ERROR,
    MSG_LAST_PENDING,
    MSG_MONITOR_PENDING,
    MSG_NO_DAEMON_STATE,
    MSG_NOTIFY_OUT_OF_SCOPE,
    MSG_STATS_PENDING,
    MSG_THROTTLED,
    MSG_UNAUTHORIZED,
    TikDownBot,
    build_caller,
    cookie_metadata_oversize,
    format_disk_message,
    format_pause_message,
    format_status_message,
    heartbeat_age_seconds,
    parse_allowed_user_ids,
    parse_chat_id,
    parse_username_arg,
    to_inline_keyboard,
    usage_for,
)
from tikdown_rs.bot.security import SecurityGuard
from tikdown_rs.core.config import Settings
from tikdown_rs.core.errors import ConfigurationError

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
class StubFile:
    """PTB ``File`` double: records the download path, writes ``size_to_write`` bytes."""

    custom_paths: list[str] = field(default_factory=list)
    download_raises: Exception | None = None
    size_to_write: int = 10

    async def download_to_drive(self, custom_path: str | None = None) -> None:
        assert custom_path is not None
        self.custom_paths.append(custom_path)
        if self.download_raises is not None:
            raise self.download_raises
        with open(custom_path, "wb") as fh:  # noqa: ASYNC230 - stub double, no real I/O path
            fh.truncate(self.size_to_write)


@dataclass
class StubDocument:
    file_size: int | None = 100
    file_to_return: StubFile | None = None
    get_file_calls: int = 0

    async def get_file(self) -> StubFile:
        self.get_file_calls += 1
        if self.file_to_return is None:
            self.file_to_return = StubFile()
        return self.file_to_return


@dataclass
class StubMessage:
    document: StubDocument | None = None
    deleted: int = 0

    async def delete(self) -> None:
        self.deleted += 1


@dataclass
class StubContext:
    args: list[str] = field(default_factory=list)


@dataclass
class StubUpdate:
    effective_chat: StubChat | None
    effective_user: StubUser | None = None
    callback_query: StubQuery | None = None
    effective_message: StubMessage | None = None


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
    ALL_COMMANDS: ClassVar[list[str]] = [
        "add",
        "backfill",
        "check",
        "cookies",
        "disk",
        "help",
        "last",
        "list",
        "monitor",
        "notify",
        "pause",
        "remove",
        "resume",
        "start",
        "stats",
        "status",
    ]

    def test_registers_all_commands_and_callback_once(self, bot_factory):
        bot, _ = bot_factory()
        bot.register_handlers()
        app = bot.application
        commands = [h.commands for h in app.handlers if hasattr(h, "commands")]
        assert sorted(c for group in commands for c in group) == self.ALL_COMMANDS
        callbacks = [h for h in app.handlers if hasattr(h, "pattern")]
        assert len(callbacks) == 1
        assert callbacks[0].pattern.pattern == r"^listp:"

    def test_registers_document_handler(self, bot_factory):
        # T5b: cookie upload via MessageHandler(filters.Document.ALL) — verified PTB import.
        bot, _ = bot_factory()
        bot.register_handlers()
        doc_handlers = [h for h in bot.application.handlers if isinstance(h, MessageHandler)]
        assert len(doc_handlers) == 1
        assert doc_handlers[0].filters is filters.Document.ALL

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


# --- T5b pure helpers --------------------------------------------------------


class TestParseUsernameArg:
    def test_first_arg(self):
        assert parse_username_arg(StubContext(args=["@User"])) == "@User"

    def test_no_args_or_blank(self):
        assert parse_username_arg(StubContext(args=[])) is None
        assert parse_username_arg(StubContext(args=["   "])) is None

    def test_none_context_is_tolerated(self):
        # Old-style tests (and any PTB edge) pass context=None; must not raise.
        assert parse_username_arg(None) is None


class TestCookieMetadataOversize:
    def test_boundary_exactly_10mb_is_allowed(self):
        assert cookie_metadata_oversize(COOKIE_MAX_UPLOAD_BYTES) is False

    def test_over_is_rejected(self):
        assert cookie_metadata_oversize(COOKIE_MAX_UPLOAD_BYTES + 1) is True

    def test_unknown_size_passes_metadata_gate(self):
        # file_size is Optional in PTB; the REAL check repeats post-download.
        assert cookie_metadata_oversize(None) is False


class TestFormatPauseMessage:
    def test_pause_fresh_and_already(self):
        assert format_pause_message("@alice", True, False) == "Cuenta @alice pausada."
        assert format_pause_message("alice", True, True) == "La cuenta @alice ya estaba pausada."

    def test_resume_fresh_and_already(self):
        assert format_pause_message("alice", False, False) == "Cuenta @alice reanudada."
        assert format_pause_message("@alice", False, True) == "La cuenta @alice ya estaba activa."

    def test_username_never_renders_doubled_at(self):
        # T-BOT-9: template carries '@', display_username strips the LEADING '@'.
        text = format_pause_message("@alice", True, False)
        assert text.startswith("Cuenta @alice")
        assert not text.startswith("Cuenta @@")


class TestUsageFor:
    def test_usage_line(self):
        assert usage_for("add") == "Uso: /add @usuario"


# --- T5b mutating commands: orchestration via service doubles ----------------


class RecordingDouble:
    """Async service double recording its args and returning a canned value."""

    def __init__(self, calls: list, result=None, raises: Exception | None = None):
        self.calls = calls
        self.result = result
        self.raises = raises

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.result


class TestAddCommand:
    async def test_calls_add_account_with_cli_defaults(self, bot_factory, monkeypatch):
        bot, factory = bot_factory()
        calls: list = []
        monkeypatch.setattr(dispatcher, "add_account", RecordingDouble(calls, result=1))
        chat = StubChat(id=111)
        update = StubUpdate(effective_chat=chat, effective_user=StubUser(id=222))
        await bot.cmd_add(update, StubContext(args=["@Alice"]))
        # Same service path as `accounts add`: (factory, user, mode='history', then_monitor=False)
        assert calls == [((factory, "@Alice", "history", False), {})]
        assert "Cuenta añadida" in chat.sent[0]["text"]
        assert "@Alice" in chat.sent[0]["text"]

    async def test_readd_is_a_business_error_not_a_crash(self, bot_factory, monkeypatch):
        # T-BOT-4 idempotency: re-adding replies the business error, not internal error.
        bot, _ = bot_factory()
        calls: list = []
        monkeypatch.setattr(
            dispatcher,
            "add_account",
            RecordingDouble(calls, raises=ConfigurationError("account already exists: <alice>")),
        )
        chat = StubChat(id=111)
        await bot.cmd_add(StubUpdate(effective_chat=chat), StubContext(args=["alice"]))
        text = chat.sent[0]["text"]
        assert "account already exists" in text
        assert MSG_INTERNAL_ERROR not in text
        assert "&lt;alice&gt;" in text  # dynamic content escaped (T-BOT-7)

    async def test_no_args_replies_usage_without_service_call(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()
        calls: list = []
        monkeypatch.setattr(dispatcher, "add_account", RecordingDouble(calls, result=1))
        chat = StubChat(id=111)
        await bot.cmd_add(StubUpdate(effective_chat=chat), StubContext(args=[]))
        assert calls == []
        assert "Uso: /add" in chat.sent[0]["text"]


class TestBackfillCommand:
    async def test_queues_via_queue_backfill(self, bot_factory, monkeypatch):
        bot, factory = bot_factory()
        calls: list = []
        monkeypatch.setattr(dispatcher, "queue_backfill", RecordingDouble(calls, result="queued"))
        chat = StubChat(id=111)
        await bot.cmd_backfill(StubUpdate(effective_chat=chat), StubContext(args=["@bob"]))
        # Same service path as `backfill run --queue`.
        assert calls == [((factory, "@bob", True), {})]
        assert "bob" in chat.sent[0]["text"]
        assert "cola" in chat.sent[0]["text"]

    async def test_backfilling_account_is_a_business_error(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()
        calls: list = []
        monkeypatch.setattr(
            dispatcher,
            "queue_backfill",
            RecordingDouble(calls, raises=ConfigurationError("cannot re-queue: backfilling")),
        )
        chat = StubChat(id=111)
        await bot.cmd_backfill(StubUpdate(effective_chat=chat), StubContext(args=["bob"]))
        assert "cannot re-queue" in chat.sent[0]["text"]
        assert MSG_INTERNAL_ERROR not in chat.sent[0]["text"]

    async def test_no_args_replies_usage(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()
        calls: list = []
        monkeypatch.setattr(dispatcher, "queue_backfill", RecordingDouble(calls))
        chat = StubChat(id=111)
        await bot.cmd_backfill(StubUpdate(effective_chat=chat), None)
        assert calls == []
        assert "Uso: /backfill" in chat.sent[0]["text"]


class TestPauseResumeCommands:
    def make_bot(self, bot_factory, monkeypatch, calls, paused_before, raises=None):
        bot, factory = bot_factory()
        monkeypatch.setattr(
            dispatcher,
            "get_account",
            RecordingDouble([], result=SimpleNamespace(paused=paused_before)),
        )
        monkeypatch.setattr(dispatcher, "set_paused", RecordingDouble(calls, raises=raises))
        return bot, factory

    async def test_pause_calls_set_paused_true_and_reports_state(self, bot_factory, monkeypatch):
        calls: list = []
        bot, factory = self.make_bot(bot_factory, monkeypatch, calls, paused_before=False)
        chat = StubChat(id=111)
        await bot.cmd_pause(StubUpdate(effective_chat=chat), StubContext(args=["alice"]))
        # Same service path as `accounts pause`.
        assert calls == [((factory, "alice", True), {})]
        assert chat.sent[0]["text"] == "Cuenta @alice pausada."

    async def test_pause_already_paused_reports_current_state(self, bot_factory, monkeypatch):
        calls: list = []
        bot, _ = self.make_bot(bot_factory, monkeypatch, calls, paused_before=True)
        chat = StubChat(id=111)
        await bot.cmd_pause(StubUpdate(effective_chat=chat), StubContext(args=["alice"]))
        # Idempotent: service still called, reply reports the state.
        assert calls and calls[0][0][2] is True
        assert chat.sent[0]["text"] == "La cuenta @alice ya estaba pausada."

    async def test_resume_calls_set_paused_false(self, bot_factory, monkeypatch):
        calls: list = []
        bot, factory = self.make_bot(bot_factory, monkeypatch, calls, paused_before=True)
        chat = StubChat(id=111)
        await bot.cmd_resume(StubUpdate(effective_chat=chat), StubContext(args=["alice"]))
        assert calls == [((factory, "alice", False), {})]
        assert chat.sent[0]["text"] == "Cuenta @alice reanudada."

    async def test_resume_already_active_reports_current_state(self, bot_factory, monkeypatch):
        calls: list = []
        bot, _ = self.make_bot(bot_factory, monkeypatch, calls, paused_before=False)
        chat = StubChat(id=111)
        await bot.cmd_resume(StubUpdate(effective_chat=chat), StubContext(args=["alice"]))
        assert chat.sent[0]["text"] == "La cuenta @alice ya estaba activa."

    async def test_unknown_account_is_business_error(self, bot_factory, monkeypatch):
        calls: list = []
        bot, _ = self.make_bot(
            bot_factory,
            monkeypatch,
            calls,
            paused_before=False,
            raises=ConfigurationError("unknown account: ghost"),
        )
        chat = StubChat(id=111)
        await bot.cmd_pause(StubUpdate(effective_chat=chat), StubContext(args=["ghost"]))
        assert "unknown account: ghost" in chat.sent[0]["text"]

    async def test_no_args_replies_usage(self, bot_factory, monkeypatch):
        calls: list = []
        bot, _ = self.make_bot(bot_factory, monkeypatch, calls, paused_before=False)
        chat = StubChat(id=111)
        await bot.cmd_resume(StubUpdate(effective_chat=chat), None)
        assert calls == []
        assert "Uso: /resume" in chat.sent[0]["text"]


class TestRemoveCommand:
    async def test_calls_remove_account_and_confirms(self, bot_factory, monkeypatch):
        bot, factory = bot_factory()
        calls: list = []
        monkeypatch.setattr(dispatcher, "remove_account", RecordingDouble(calls, result=None))
        chat = StubChat(id=111)
        await bot.cmd_remove(StubUpdate(effective_chat=chat), StubContext(args=["@carol"]))
        # Same service path as `accounts remove`.
        assert calls == [((factory, "@carol"), {})]
        assert "eliminada" in chat.sent[0]["text"]
        assert "@carol" in chat.sent[0]["text"]

    async def test_blocked_remove_is_business_error(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()
        calls: list = []
        monkeypatch.setattr(
            dispatcher,
            "remove_account",
            RecordingDouble(calls, raises=ConfigurationError("still has 3 video(s)")),
        )
        chat = StubChat(id=111)
        await bot.cmd_remove(StubUpdate(effective_chat=chat), StubContext(args=["carol"]))
        assert "still has 3 video(s)" in chat.sent[0]["text"]
        assert MSG_INTERNAL_ERROR not in chat.sent[0]["text"]


class TestInformativeCommands:
    async def test_monitor_replies_pending_without_service(self, bot_factory):
        bot, factory = bot_factory()
        chat = StubChat(id=111)
        await bot.cmd_monitor(StubUpdate(effective_chat=chat), None)
        assert MSG_MONITOR_PENDING in chat.sent[0]["text"]
        assert factory.entries == 0

    async def test_check_replies_pending_without_service(self, bot_factory):
        bot, factory = bot_factory()
        chat = StubChat(id=111)
        await bot.cmd_check(StubUpdate(effective_chat=chat), None)
        assert MSG_CHECK_PENDING in chat.sent[0]["text"]
        assert factory.entries == 0

    async def test_notify_replies_out_of_scope(self, bot_factory):
        # §17.1 [F]: push notifications are out of base scope; informative only.
        bot, factory = bot_factory()
        chat = StubChat(id=111)
        await bot.cmd_notify(StubUpdate(effective_chat=chat), None)
        assert MSG_NOTIFY_OUT_OF_SCOPE in chat.sent[0]["text"]
        assert factory.entries == 0


# --- T5b cookie upload funnel -------------------------------------------------


class TestCookiesCommand:
    async def test_no_args_replies_instructions(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()
        calls: list = []
        monkeypatch.setattr(dispatcher, "add_cookie", RecordingDouble(calls))
        chat = StubChat(id=111)
        await bot.cmd_cookies(StubUpdate(effective_chat=chat), None)
        assert calls == []
        assert MSG_COOKIE_INSTRUCTIONS in chat.sent[0]["text"]


class TestCookieUpload:
    def make_update(
        self, chat_id: int = 111, file_size: int | None = 100, file: StubFile | None = None
    ) -> StubUpdate:
        document = StubDocument(file_size=file_size, file_to_return=file)
        message = StubMessage(document=document)
        return StubUpdate(
            effective_chat=StubChat(id=chat_id),
            effective_user=StubUser(id=222),
            effective_message=message,
        )

    async def test_oversize_metadata_rejected_before_download(self, bot_factory, monkeypatch):
        bot, factory = bot_factory()
        calls: list = []
        monkeypatch.setattr(dispatcher, "add_cookie", RecordingDouble(calls))
        update = self.make_update(file_size=COOKIE_MAX_UPLOAD_BYTES + 1)
        await bot.on_cookie_document(update, None)
        # Rejected on metadata, BEFORE any download or import.
        assert update.effective_message.document.get_file_calls == 0
        assert calls == []
        assert factory.entries == 0
        assert MSG_COOKIE_TOO_LARGE in update.effective_chat.sent[0]["text"]

    async def test_happy_path_imports_and_cleans_tempfile(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()
        calls: list = []
        existed_at_call: list[bool] = []

        async def fake_add_cookie(f, path, label, keep_source):
            calls.append((f, path, label, keep_source))
            existed_at_call.append(os.path.exists(path))
            return 7

        monkeypatch.setattr(dispatcher, "add_cookie", fake_add_cookie)
        file = StubFile(size_to_write=10)
        update = self.make_update(file=file)
        await bot.on_cookie_document(update, None)
        # mkstemp pattern: the tempfile existed for the import and is gone after.
        assert len(calls) == 1
        assert existed_at_call == [True]
        assert calls[0][2] is None and calls[0][3] is False
        assert not os.path.exists(calls[0][1])  # T-COOKIES-7: always deleted
        assert update.effective_message.deleted == 1  # §7 privacy: best-effort delete
        assert "id=7" in update.effective_chat.sent[0]["text"]

    async def test_download_failure_still_deletes_tempfile(self, bot_factory, monkeypatch):
        # T-COOKIES-7: the finally-deletion holds even when the download raises.
        bot, _ = bot_factory()
        calls: list = []
        monkeypatch.setattr(dispatcher, "add_cookie", RecordingDouble(calls))
        file = StubFile(download_raises=RuntimeError("network exploded"))
        update = self.make_update(file=file)
        await bot.on_cookie_document(update, None)
        assert file.custom_paths and not os.path.exists(file.custom_paths[0])
        assert calls == []
        assert MSG_INTERNAL_ERROR in update.effective_chat.sent[0]["text"]

    async def test_oversize_real_size_rejected_after_download(self, bot_factory, monkeypatch):
        bot, _ = bot_factory()
        calls: list = []
        monkeypatch.setattr(dispatcher, "add_cookie", RecordingDouble(calls))
        file = StubFile(size_to_write=COOKIE_MAX_UPLOAD_BYTES + 1024)
        update = self.make_update(file=file)
        await bot.on_cookie_document(update, None)
        # Metadata passed (100 bytes) but the REAL post-download size rejects.
        assert update.effective_message.document.get_file_calls == 1
        assert calls == []
        assert MSG_COOKIE_TOO_LARGE in update.effective_chat.sent[0]["text"]
        assert not os.path.exists(file.custom_paths[0])
        assert update.effective_message.deleted == 0  # nothing imported: no privacy delete

    async def test_unauthorized_document_is_denied_before_download(self, bot_factory, monkeypatch):
        bot, factory = bot_factory()
        calls: list = []
        monkeypatch.setattr(dispatcher, "add_cookie", RecordingDouble(calls))
        update = self.make_update(chat_id=999)
        await bot.on_cookie_document(update, None)
        # Authz double-layer first (§6.3): no download, no import.
        assert update.effective_message.document.get_file_calls == 0
        assert calls == []
        assert factory.entries == 0
        assert MSG_UNAUTHORIZED in update.effective_chat.sent[0]["text"]

    async def test_throttle_applies_to_uploads(self, bot_factory, monkeypatch):
        clock, tick = make_clock()
        bot, _ = bot_factory(guard=SecurityGuard(111, clock=tick))
        calls: list = []
        monkeypatch.setattr(dispatcher, "add_cookie", RecordingDouble(calls))
        first = self.make_update(file=StubFile(size_to_write=10))
        await bot.on_cookie_document(first, None)
        clock["now"] += 0.5  # inside the 2 s window
        second = self.make_update(file=StubFile(size_to_write=10))
        await bot.on_cookie_document(second, None)
        assert MSG_THROTTLED in second.effective_chat.sent[0]["text"]
        assert second.effective_message.document.get_file_calls == 0
        assert first.effective_message.document.get_file_calls == 1

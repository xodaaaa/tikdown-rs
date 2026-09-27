"""Notification catalog, render and channel contract (6.2).

Trampas: T-DATA-9 (parity: every catalog event has a real producer or is
marked deferred-with-milestone; the scan EXCLUDES events.py -- without that
exclusion the assertion is vacuous, T-BOT-13), T-BACKFILL-15 (the emission
channel is synchronous, never a coroutine).
"""

import ast
import inspect
import re
from pathlib import Path

import pytest

from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.core.notifications import (
    EVENTS,
    EmittedEvent,
    InMemoryNotificationService,
    NoopNotificationService,
    NotificationService,
    render,
)

# --- catalog shape (6.2) ---

REQUIRED_EVENTS = {
    "daemon.started",
    "daemon.stopped",
    "daemon.db_contention",
    "monitor.started",
    "monitor.stopped",
    "monitor.stopped_no_cookies",
    "monitor.video_discovered",
    "download.downloaded",
    "download.skipped",
    "download.failed",
    "backfill.started",
    "backfill.completed",
    "backfill.cancelled",
    "backfill.paused",
    "backfill.no_cookies",
    "cookie.validation_probe_failed",
    "cookie.validated",
    "network.offline",
    "network.online",
    "disk.warning",
    "disk.paused",
    "disk.resumed",
    "selfcheck.failed",
    "selfcheck.ok",
    "profile.refreshed",
    "bot.unauthorized_attempt",
    "bot.unauthorized_attempts_burst",
    "bot.started",
    "bot.stopped",
}


def test_catalog_contains_exactly_the_required_events() -> None:
    assert set(EVENTS) == REQUIRED_EVENTS


def test_event_names_are_dotted_ascii_and_templates_are_small() -> None:
    for name, spec in EVENTS.items():
        assert re.fullmatch(r"[a-z_]+(?:\.[a-z_]+)+", name), name
        assert spec.name == name
        assert spec.template.isascii() and len(spec.template) <= 4096
        assert spec.description, name
        assert spec.deferred is None or re.fullmatch(r"M\d+", spec.deferred), name


def test_templates_never_use_format_specs() -> None:
    # render() tolerates missing keys by inserting a placeholder STRING; a
    # format spec would raise on it. Job paths must never crash (6.2).
    for spec in EVENTS.values():
        assert not re.search(r"\{[^{}]*:", spec.template), spec.name


# --- render (6.2) ---


def test_render_known_event_formats_payload() -> None:
    assert (
        render("daemon.started", {"pid": 123, "data_dir": "/data"})
        == "Daemon started (pid 123, data_dir /data)."
    )


def test_render_tolerates_missing_payload_keys() -> None:
    # Never raise on payload gaps: render what is available (6.2).
    assert render("daemon.started", {}) == "Daemon started (pid {pid}, data_dir {data_dir})."


def test_render_unknown_event_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError):
        render("not.an.event", {})


# --- channel contract (T-BACKFILL-15: synchronous) ---


def test_noop_service_emits_nothing() -> None:
    assert NoopNotificationService().emit("daemon.started", {"pid": 1}) is None


def test_inmemory_service_records_event_payload_timestamp_in_order() -> None:
    service = InMemoryNotificationService()
    service.emit("daemon.started", {"pid": 1})
    service.emit("download.downloaded", {"video_id": "v"})
    first, second = service.events
    assert isinstance(first, EmittedEvent)
    assert first.event == "daemon.started" and first.payload == {"pid": 1}
    assert second.event == "download.downloaded" and second.payload == {"video_id": "v"}
    assert first.timestamp <= second.timestamp


def test_channel_is_synchronous_not_async() -> None:
    assert not inspect.iscoroutinefunction(NoopNotificationService.emit)
    assert not inspect.iscoroutinefunction(InMemoryNotificationService.emit)
    assert not inspect.iscoroutinefunction(NotificationService.emit)
    service = InMemoryNotificationService()
    assert not inspect.iscoroutine(service.emit("daemon.started", {}))


def test_services_satisfy_the_notification_protocol() -> None:
    assert isinstance(NoopNotificationService(), NotificationService)
    assert isinstance(InMemoryNotificationService(), NotificationService)


# --- parity: template <-> producer (T-DATA-9, T-BOT-13) ---

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "tikdown_rs"
EVENTS_FILE = SRC_ROOT / "core" / "notifications" / "events.py"
_EVENT_NAME_RE = re.compile(r"[a-z_]+(?:\.[a-z_]+)+")


def _scan_sources() -> tuple[set[str], set[str]]:
    """AST string literals + Name ids of every src/ module EXCEPT events.py.

    Excluding the catalog file is what keeps this assertion non-vacuous
    (T-BOT-13): a string only in events.py is a template, not a producer.
    """
    strings: set[str] = set()
    names: set[str] = set()
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path == EVENTS_FILE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.add(node.value)
            elif isinstance(node, ast.Name):
                names.add(node.id)
    return strings, names


def _catalog_name_constants() -> dict[str, str]:
    """EVENT_X = 'dotted.name' assignments in events.py -> constant -> name."""
    tree = ast.parse(EVENTS_FILE.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and _EVENT_NAME_RE.fullmatch(node.value.value)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    mapping[target.id] = node.value.value
    return mapping


def _is_produced(name: str, strings: set[str], constants: dict[str, str], used: set[str]) -> bool:
    """Produced = the event-name literal or its EVENT_* constant appears in src/."""
    return name in strings or any(
        event == name and constant in used for constant, event in constants.items()
    )


def test_every_non_deferred_event_has_a_real_producer() -> None:
    # T-DATA-9: an event without a producer is never emitted, silently.
    strings, names = _scan_sources()
    constants = _catalog_name_constants()
    unproduced = sorted(
        name
        for name, spec in EVENTS.items()
        if spec.deferred is None and not _is_produced(name, strings, constants, names)
    )
    assert unproduced == []


def test_every_deferred_event_has_no_producer_and_matches_its_milestone() -> None:
    strings, names = _scan_sources()
    constants = _catalog_name_constants()
    for name, spec in EVENTS.items():
        if spec.deferred is None:
            continue
        assert re.fullmatch(r"M\d+", spec.deferred), name
        assert not _is_produced(name, strings, constants, names), (
            f"{name} already has a producer: remove deferred='{spec.deferred}' "
            "from the catalog (one-word edit)"
        )


def test_bot_events_are_deferred_to_m5() -> None:
    for name in (
        "bot.unauthorized_attempt",
        "bot.unauthorized_attempts_burst",
        "bot.started",
        "bot.stopped",
    ):
        assert EVENTS[name].deferred == "M5"

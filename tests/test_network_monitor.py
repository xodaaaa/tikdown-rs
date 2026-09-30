"""core.network_monitor: the online/offline state machine (8.1).

Trampas covered: T-ENGINE-7 (the event is INJECTED and pre-set by the caller),
T-BOT-14 (a blip never emits network.online), T-BACKFILL-17 (a network failure
never reaches cookie validation state). Regla: 8.1.

All doubles are deterministic: injected async probe fakes and an injectable
monotonic clock. No real sleeps.
"""

import ast
import asyncio
from pathlib import Path

import pytest

from tikdown_rs.core.config import Settings
from tikdown_rs.core.network_monitor import NetworkMonitor, _default_probe
from tikdown_rs.core.notifications.events import (
    EVENT_NETWORK_OFFLINE,
    EVENT_NETWORK_ONLINE,
)

SOURCE_FILE = (
    Path(__file__).resolve().parents[1] / "src" / "tikdown_rs" / "core" / "network_monitor.py"
)


class FakeClock:
    """Injectable monotonic clock with manual advance."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_probe(results: list[bool]):
    """Async probe double: pops results in order, records (url, timeout) calls."""
    calls: list[tuple[str, int]] = []

    async def probe(url: str, timeout_seconds: int) -> bool:
        calls.append((url, timeout_seconds))
        return results.pop(0)

    return probe, calls


def make_monitor(results: list[bool], clock: FakeClock | None = None, **kwargs):
    settings = Settings(
        network_probe_url="https://probe.example.com/generate_204",
        network_probe_timeout_seconds=5,
        network_offline_threshold_consecutive_failures=2,
    )
    probe, calls = make_probe(results)
    clock = clock or FakeClock()
    monitor = NetworkMonitor(
        settings,
        asyncio.Event(),
        probe_fn=probe,
        clock_fn=clock,
        **kwargs,
    )
    monitor.network_available.set()  # T-ENGINE-7: the CALLER creates it pre-set
    return monitor, calls, clock


# --- B4 fix (JD-A-005): empty NETWORK_PROBE_URL must NOT self-classify offline ---


async def test_empty_probe_url_assumes_online_and_never_probes() -> None:
    """§8.1 + §0.2.3: the probe URL is NOT in the fail-fast critical list, so an
    empty default degrades to 'network assumed available' with a warning, never
    to a permanent false offline (two httpx failures on '')."""
    settings = Settings(network_probe_url="")
    boom = False

    async def exploding_probe(url: str, timeout_seconds: int) -> bool:
        nonlocal boom
        boom = True
        return False

    monitor = NetworkMonitor(settings, asyncio.Event(), probe_fn=exploding_probe)
    monitor.network_available.set()  # T-ENGINE-7: caller creates it pre-set
    assert await monitor.probe_once() is True
    assert boom is False  # no probe call: an empty URL must never be probed
    assert not monitor._offline
    assert monitor.network_available.is_set()
    assert monitor.offline_since is None


# --- T-ENGINE-7: injected event, pre-set by the caller ---


async def test_fresh_monitor_does_not_clear_the_pre_set_event() -> None:
    """T-ENGINE-7: a monitor that never probed must leave the caller's pre-set
    event alone: without the monitor the network is assumed available."""
    settings = Settings(network_probe_url="https://probe.example.com/generate_204")
    event = asyncio.Event()
    event.set()
    monitor = NetworkMonitor(settings, event, probe_fn=None, clock_fn=FakeClock())

    assert event.is_set()
    assert not monitor._offline
    assert monitor.offline_since is None


async def test_success_keeps_the_event_set() -> None:
    monitor, _, _ = make_monitor([True])
    assert await monitor.probe_once() is True
    assert monitor.network_available.is_set()
    assert not monitor._offline


# --- blip vs confirmed offline (T-BOT-14) ---


async def test_one_failure_is_a_blip_no_event_no_offline_since() -> None:
    monitor, _, _ = make_monitor([False])
    events: list[dict] = []
    monitor._on_event = events.append

    assert await monitor.probe_once() is False
    assert not monitor._offline
    assert monitor.network_available.is_set()  # blip never clears the event
    assert monitor.offline_since is None
    assert events == []


async def test_two_consecutive_failures_confirm_offline() -> None:
    clock = FakeClock()
    monitor, _, _ = make_monitor([False, False], clock)
    events: list[dict] = []
    monitor._on_event = events.append

    assert await monitor.probe_once() is False
    assert not monitor._offline  # threshold not reached yet

    clock.advance(30)
    assert await monitor.probe_once() is False
    assert monitor._offline
    assert not monitor.network_available.is_set()
    assert monitor.offline_since == clock.now  # captured AT the threshold crossing
    assert [e["event"] for e in events] == [EVENT_NETWORK_OFFLINE]
    assert events[0]["failures"] == 2


async def test_success_after_confirmed_offline_emits_online_with_duration() -> None:
    clock = FakeClock()
    monitor, _, _ = make_monitor([False, False, True], clock)
    events: list[dict] = []
    monitor._on_event = events.append

    await monitor.probe_once()
    clock.advance(30)
    await monitor.probe_once()  # offline confirmed at t=1030
    clock.advance(95)

    assert await monitor.probe_once() is True
    assert monitor.network_available.is_set()
    assert not monitor._offline
    assert monitor.offline_since is None
    online = [e for e in events if e["event"] == EVENT_NETWORK_ONLINE]
    assert len(online) == 1
    assert online[0]["seconds"] == 95  # outage duration captured BEFORE clearing


async def test_success_after_blip_emits_no_online_event() -> None:
    """T-BOT-14: a blip of duration 0 must not produce a misleading notification."""
    monitor, _, _ = make_monitor([False, True])
    events: list[dict] = []
    monitor._on_event = events.append

    await monitor.probe_once()  # blip
    await monitor.probe_once()  # back to green

    assert [e["event"] for e in events] == []  # NO network.online
    assert not monitor._offline


async def test_repeated_failures_emit_offline_only_once() -> None:
    monitor, _, _ = make_monitor([False] * 4)
    events: list[dict] = []
    monitor._on_event = events.append

    for _ in range(4):
        await monitor.probe_once()

    assert [e["event"] for e in events] == [EVENT_NETWORK_OFFLINE]  # once, not per failure


# --- the probe never touches TikTok: URL comes from settings ---


async def test_injected_probe_receives_exactly_the_settings_url_and_timeout() -> None:
    monitor, calls, _ = make_monitor([True])
    await monitor.probe_once()
    assert calls == [("https://probe.example.com/generate_204", 5)]


async def test_default_probe_counts_any_response_below_500_as_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class FakeResponse:
        status_code = 204

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def head(self, url: str) -> FakeResponse:
            captured["url"] = url
            return FakeResponse()

    monkeypatch.setattr("tikdown_rs.core.network_monitor.httpx.AsyncClient", FakeClient)

    assert await _default_probe("https://probe.example.com/generate_204", 5) is True
    assert captured["timeout"] == 5
    assert captured["url"] == "https://probe.example.com/generate_204"

    FakeResponse.status_code = 503
    assert await _default_probe("https://probe.example.com/generate_204", 5) is False


async def test_default_probe_counts_connection_errors_as_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BoomClient:
        def __init__(self, **kwargs) -> None: ...

        async def __aenter__(self):
            raise OSError("connection refused")

        async def __aexit__(self, *exc_info):
            return False

    monkeypatch.setattr("tikdown_rs.core.network_monitor.httpx.AsyncClient", BoomClient)
    assert await _default_probe("https://probe.example.com/generate_204", 5) is False


# --- T-BACKFILL-17: a network failure never reaches cookie state ---


def test_network_monitor_never_imports_cookie_services() -> None:
    """T-BACKFILL-17 (structural): the monitor module must not import anything
    from services/ -- a network failure can never produce
    validation_state='invalid' or consume retries because the code path does
    not exist."""
    tree = ast.parse(SOURCE_FILE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(alias.name.startswith("tikdown_rs.services") for alias in node.names), (
                node
            )
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("tikdown_rs.services"), node

"""WU4b: monitor start/stop service + CLI, degraded gate, daemon status extensions.

Trampas covered: T-DB-14 (contention read from daemon_state, never the CLI
process), T-ASYNC-14 (supervised tasks / zombie threads exposed honestly),
4.1 (degraded daemon rejects monitor start and backfill run with an
actionable error), T-BACKFILL-12 ordering (the degradation gate runs BEFORE
the cookie gate), T-CLI-1 (ASCII output). Reglas: 10.1, 10.2, 4.1, 5.5, 5.6.

Honesty contract (T-ASYNC-14): supervised tasks and zombie yt-dlp threads are
IN-PROCESS counters. A `daemon status` CLI invocation is a separate process
whose registry is always empty, so the CLI prints 'n/a (in-process)' instead
of a fake 0; the formatter accepts real counts when a caller HAS the objects
(daemon process / future M5 bot).
"""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from typer.testing import CliRunner

from tikdown_rs.cli.daemon import _format_status_lines
from tikdown_rs.cli.main import app
from tikdown_rs.core.daemon_state import read_status, record_selfcheck, write_heartbeat
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.core.notifications import InMemoryNotificationService
from tikdown_rs.core.notifications.events import (
    EVENT_MONITOR_STARTED,
    EVENT_MONITOR_STOPPED,
)
from tikdown_rs.models import Cookie, DaemonState, MonitoredAccount, Video
from tikdown_rs.services.monitor_state import start_monitor, stop_monitor

runner = CliRunner()


@pytest.fixture
async def factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Session factory over a REAL migrated file database (T-DATA-1 pattern)."""
    await asyncio.to_thread(run_migrations, tmp_path)
    engine = create_db_engine(sqlite_url_for(tmp_path))
    session_factory = make_session_factory(engine)
    yield session_factory
    await engine.dispose()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """CLI DATA_DIR pointed at a migrated tmp database (daemon-test pattern)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    run_migrations(tmp_path)
    return tmp_path


async def set_degraded(factory, reason: str = "impersonation: curl_cffi-missing") -> None:
    async with factory() as session:
        await record_selfcheck(session, ok=False, degraded_reason=reason)


async def get_state(factory) -> DaemonState | None:
    async with factory() as session:
        return await read_status(session)


def _all_output(result) -> str:
    try:
        return result.output + result.stderr
    except ValueError:  # click < 8.2: stderr already mixed into output
        return result.output


# --- start_monitor / stop_monitor (service) ---


async def test_start_monitor_persists_flag_and_emits_once(factory) -> None:
    notifications = InMemoryNotificationService()
    on_event = lambda payload: notifications.emit(
        payload["event"], {k: v for k, v in payload.items() if k != "event"}
    )

    await start_monitor(factory, on_event=on_event)

    state = await get_state(factory)
    assert state is not None and state.monitor_running
    started = [e for e in notifications.events if e.event == EVENT_MONITOR_STARTED]
    assert len(started) == 1


async def test_start_monitor_idempotent_no_second_event(factory) -> None:
    on_event_calls: list[dict] = []

    def on_event(payload: dict) -> None:
        on_event_calls.append(payload)

    await start_monitor(factory, on_event=on_event)
    await start_monitor(factory, on_event=on_event)

    assert len([c for c in on_event_calls if c["event"] == EVENT_MONITOR_STARTED]) == 1


async def test_start_monitor_refuses_when_degraded_with_remediation(factory) -> None:
    await set_degraded(factory, "impersonation: curl_cffi-missing")

    with pytest.raises(Exception) as exc_info:  # ConfigurationError
        await start_monitor(factory)
    message = str(exc_info.value)
    assert "degraded" in message
    assert "impersonation: curl_cffi-missing" in message
    # Actionable remediation (4.1): name the selfcheck fix path.
    assert "selfcheck" in message
    state = await get_state(factory)
    assert state is not None and not state.monitor_running  # gate BEFORE any state change


async def test_stop_monitor_persists_flag_and_emits(factory) -> None:
    await start_monitor(factory)
    stopped_calls: list[dict] = []

    def on_event(payload: dict) -> None:
        stopped_calls.append(payload)

    await stop_monitor(factory, on_event=on_event)

    state = await get_state(factory)
    assert state is not None and not state.monitor_running
    assert [c["event"] for c in stopped_calls] == [EVENT_MONITOR_STOPPED]


async def test_stop_monitor_idempotent(factory) -> None:
    await stop_monitor(factory)  # never started: no error
    state = await get_state(factory)
    assert state is not None and not state.monitor_running


# --- CLI monitor start/stop ---


def test_cli_monitor_start_stop(data_dir, factory) -> None:
    result = runner.invoke(app, ["monitor", "start"])
    assert result.exit_code == 0, _all_output(result)
    assert "monitor started" in result.output
    result.output.encode("ascii")  # T-CLI-1

    row = asyncio.run(get_state(factory))
    assert row is not None and row.monitor_running

    result = runner.invoke(app, ["monitor", "stop"])
    assert result.exit_code == 0, _all_output(result)
    assert "monitor stopped" in result.output
    row = asyncio.run(get_state(factory))
    assert row is not None and not row.monitor_running


def test_cli_monitor_start_degraded_exits_1(data_dir, factory) -> None:
    asyncio.run(set_degraded(factory))

    result = runner.invoke(app, ["monitor", "start"])

    assert result.exit_code == 1
    assert "ERROR" in _all_output(result)
    assert "degraded" in _all_output(result)
    assert "selfcheck" in _all_output(result)


# --- backfill run degraded gate (4.1): gate BEFORE cookies (T-BACKFILL-12 order) ---


async def add_account(factory, username: str = "acct") -> int:
    async with factory() as session:
        account = MonitoredAccount(username=username, backfill_status="idle")
        session.add(account)
        await session.commit()
        return account.id


def test_cli_backfill_run_gated_when_degraded(data_dir, factory, monkeypatch) -> None:
    asyncio.run(add_account(factory))
    asyncio.run(set_degraded(factory, "impersonation: no targets"))
    # T-BACKFILL-12 ordering: if the cookie gate ran first, the error would be
    # about cookies, not the degradation. Guard: no cookies exist at all here.

    result = runner.invoke(app, ["backfill", "run", "@acct", "--queue"])

    assert result.exit_code == 1
    out = _all_output(result)
    assert "ERROR" in out
    assert "degraded" in out
    assert "impersonation: no targets" in out
    assert "selfcheck" in out
    # Nothing was queued: the gate runs before any state change.
    async_state = asyncio.run(get_state(factory))
    assert async_state is not None

    async def backfill_status() -> str:
        async with factory() as session:
            account = (await session.execute(select(MonitoredAccount))).scalars().first()
            return account.backfill_status

    assert asyncio.run(backfill_status()) == "idle"


# --- daemon status extensions (T-DB-14, T-ASYNC-14, 10.1) ---


def test_status_shows_contention_recent_errors_and_honest_counters(data_dir, factory) -> None:
    async def seed() -> None:
        async with factory() as session:
            await write_heartbeat(session, db_busy_count=7)
            account = MonitoredAccount(username="acct", backfill_status="idle")
            session.add(account)
            await session.flush()
            session.add(
                Video(
                    tiktok_video_id="v1",
                    account_id=account.id,
                    status="failed",
                    error_category="transient",
                    error_message="boom one",
                    updated_at="2025-01-01T00:00:00+00:00",
                )
            )
            session.add(
                Video(
                    tiktok_video_id="v2",
                    account_id=account.id,
                    status="failed",
                    error_category="definitive",
                    error_message="login required",
                    updated_at="2025-01-02T00:00:00+00:00",
                )
            )
            await session.commit()

    asyncio.run(seed())

    result = runner.invoke(app, ["daemon", "status"])

    assert result.exit_code == 0, _all_output(result)
    out = result.output
    # T-DB-14: contention read from daemon_state, not the CLI process.
    assert "db_busy_count_5min: 7" in out
    # Recent errors derived from the videos table (10.1: no new table).
    assert "recent_errors: 2" in out
    assert "v2" in out and "definitive" in out and "login required" in out
    assert "v1" in out and "transient" in out
    # Honest in-process counters (T-ASYNC-14): the CLI process cannot see the
    # daemon's registry/engine, so it says n/a instead of faking a 0.
    assert "supervised_tasks: n/a (in-process)" in out
    assert "ytdlp_zombie_threads: n/a (in-process)" in out
    assert "degraded_reason: none" in out
    out.encode("ascii")


def test_status_formatter_shows_real_counts_when_injected() -> None:
    """T-ASYNC-14 seam: a caller holding the live objects prints real counts."""
    row = DaemonState(id=1, monitor_running=True, db_busy_count_5min=2)
    lines = _format_status_lines(row, {}, supervised_count=3, zombie_count=2)
    assert "supervised_tasks: 3" in lines
    assert "ytdlp_zombie_threads: 2" in lines


def test_status_lists_at_most_five_recent_errors(data_dir, factory) -> None:
    async def seed() -> None:
        async with factory() as session:
            await write_heartbeat(session)
            account = MonitoredAccount(username="acct", backfill_status="idle")
            session.add(account)
            await session.flush()
            for i in range(7):
                session.add(
                    Video(
                        tiktok_video_id=f"v{i}",
                        account_id=account.id,
                        status="failed",
                        error_category="transient",
                        error_message="boom",
                        updated_at=f"2025-01-0{i + 1}T00:00:00+00:00",
                    )
                )
            await session.commit()

    asyncio.run(seed())

    result = runner.invoke(app, ["daemon", "status"])

    assert result.exit_code == 0, _all_output(result)
    assert "recent_errors: 5" in result.output


def test_status_truncates_long_error_messages(data_dir, factory) -> None:
    async def seed() -> None:
        async with factory() as session:
            await write_heartbeat(session)
            account = MonitoredAccount(username="acct", backfill_status="idle")
            session.add(account)
            await session.flush()
            session.add(
                Video(
                    tiktok_video_id="vlong",
                    account_id=account.id,
                    status="failed",
                    error_category="transient",
                    error_message="x" * 500,
                    updated_at="2025-01-01T00:00:00+00:00",
                )
            )
            await session.commit()

    asyncio.run(seed())

    result = runner.invoke(app, ["daemon", "status"])

    assert result.exit_code == 0
    assert "x" * 500 not in result.output


# --- healthcheck degradation check (4.1, 10.1) ---


def test_healthcheck_degraded_exits_1(data_dir, factory) -> None:
    asyncio.run(set_degraded(factory, "impersonation: curl_cffi-missing"))

    async def fresh_heartbeat() -> None:
        async with factory() as session:
            await write_heartbeat(session)

    asyncio.run(fresh_heartbeat())

    result = runner.invoke(app, ["daemon", "healthcheck"])

    assert result.exit_code == 1
    out = _all_output(result)
    assert "degraded" in out
    assert "impersonation: curl_cffi-missing" in out


def test_healthcheck_all_ok_exits_0(data_dir, factory, monkeypatch) -> None:
    """4.1: degraded_reason NULL + fresh heartbeat + valid cookie -> healthy."""
    import shutil
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")

    def fake_disk_usage(path):
        return usage(total=1_000_000, used=500_000, free=500_000)  # 50% free

    monkeypatch.setattr(shutil, "disk_usage", fake_disk_usage)

    async def seed() -> None:
        async with factory() as session:
            await write_heartbeat(session)
            session.add(Cookie(label="c", cookie_blob=b"netscape", validation_state="valid"))
            await session.commit()

    asyncio.run(seed())

    result = runner.invoke(app, ["daemon", "healthcheck"])

    assert result.exit_code == 0, _all_output(result)


def test_events_monitor_started_stopped_are_active() -> None:
    """The M4 deferral is lifted: producers exist, deferred=None (T-DATA-9)."""
    from tikdown_rs.core.notifications.events import EVENTS

    assert EVENTS[EVENT_MONITOR_STARTED].deferred is None
    assert EVENTS[EVENT_MONITOR_STOPPED].deferred is None

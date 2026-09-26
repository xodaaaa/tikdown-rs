"""Daemon lifecycle tests: startup order, stop flag wiring, real subprocess smoke.

Deterministic: tmp_path DATA_DIR, real file DB, no network, heartbeat-only
scheduler. Traps covered: T-CLI-6, T-CLI-7, T-ASYNC-3, T-ASYNC-4, T-DEPLOY-8.
Rules: 5.1, 5.2, 11.2, 13.2 (daemon mandatory cases).
"""

import asyncio
import contextlib
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

import tikdown_rs.core.verify as verify_module
import tikdown_rs.daemon.run as daemon_run
from tikdown_rs.core.config import load_settings
from tikdown_rs.core.daemon_state import read_status, set_stop_requested
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.daemon.run import run_daemon_lifecycle
from tikdown_rs.models import Base
from tikdown_rs.models.daemon_state import DaemonState
from tikdown_rs.services.selfcheck import run_selfcheck

DB_FILE = "tikdown-rs.db"
POLL_STEP_SECONDS = 0.05


def _all_output(result) -> str:
    """stdout + stderr across click versions (mix_stderr differences)."""
    try:
        return result.output + result.stderr
    except ValueError:  # click < 8.2: stderr already mixed into output
        return result.output


def _fake_which(missing: set[str] = frozenset()):
    def fake(name):
        return None if name in missing else f"/fake-bin/{name}"

    return fake


async def _read_row(data_dir: Path) -> DaemonState | None:
    engine = create_db_engine(sqlite_url_for(data_dir))
    try:
        async with make_session_factory(engine)() as session:
            return await read_status(session)
    except OperationalError as exc:
        # Startup races: migrations may still be creating the schema ("no such
        # table"), and the one-time delete->WAL conversion takes an exclusive
        # lock that bypasses busy_timeout ("database is locked"). Keep polling.
        if "no such table" in str(exc) or "database is locked" in str(exc):
            return None
        raise
    finally:
        await engine.dispose()


async def _wait_for_row(data_dir: Path, predicate, deadline_seconds: float = 15.0):
    """Poll the DB from an independent engine until predicate(row) holds."""
    engine = create_db_engine(sqlite_url_for(data_dir))
    try:
        end = time.monotonic() + deadline_seconds
        row = None
        while time.monotonic() < end:
            row = await _read_row(data_dir)
            if row is not None and predicate(row):
                return row
            await asyncio.sleep(POLL_STEP_SECONDS)
        raise AssertionError(f"daemon_state predicate not met within {deadline_seconds}s: {row}")
    finally:
        await engine.dispose()


async def _set_stop_flag(data_dir: Path) -> None:
    """Simulate the second process of 13.2 writing stop_requested from its own session."""
    engine = create_db_engine(sqlite_url_for(data_dir))
    try:
        async with make_session_factory(engine)() as session:
            await set_stop_requested(session)
    finally:
        await engine.dispose()


async def _run_until_stopped(settings) -> None:
    """Drive the lifecycle as a task; requires an external stop to finish it."""
    lifecycle = asyncio.create_task(run_daemon_lifecycle(settings))
    try:
        await asyncio.wait_for(lifecycle, timeout=30.0)
    finally:
        if not lifecycle.done():
            lifecycle.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await lifecycle


@pytest.mark.timeout(60)
async def test_full_lifecycle_stops_on_stop_requested(tmp_path, monkeypatch) -> None:
    """T-CLI-6 in-process: external stop flag -> clean shutdown, state cleaned (5.2)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEARTBEAT_INTERVAL_SECONDS", "1")
    settings = load_settings()

    lifecycle = asyncio.create_task(run_daemon_lifecycle(settings))
    try:
        row = await _wait_for_row(tmp_path, lambda r: r.last_heartbeat_at is not None)
        assert row.daemon_pid == os.getpid()
        assert not row.monitor_running  # 5.1 step 6: monitor starts stopped
        await _set_stop_flag(tmp_path)
        await asyncio.wait_for(lifecycle, timeout=30.0)
    finally:
        if not lifecycle.done():
            lifecycle.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await lifecycle

    row = await _read_row(tmp_path)
    assert row is not None
    assert row.daemon_pid is None  # 5.2 step 8: daemon_state cleanup
    assert not row.stop_requested
    assert not row.monitor_running


@pytest.mark.timeout(60)
async def test_inherited_stop_flag_does_not_block_startup(tmp_path, monkeypatch) -> None:
    """T-CLI-7: seeded stop_requested is cleared at startup, not obeyed."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEARTBEAT_INTERVAL_SECONDS", "1")
    # Pre-existing schema; off-loop like any real caller would (T-ASYNC-4).
    await asyncio.to_thread(run_migrations, tmp_path)
    await _set_stop_flag(tmp_path)

    settings = load_settings()
    lifecycle = asyncio.create_task(run_daemon_lifecycle(settings))
    try:
        # The daemon must NOT exit immediately: it keeps running and heartbeating.
        await _wait_for_row(tmp_path, lambda r: r.last_heartbeat_at is not None)
        row = await _read_row(tmp_path)
        assert not row.stop_requested  # cleared before anything else (5.1 step 2)
        # Only a NEW stop request shuts it down.
        await _set_stop_flag(tmp_path)
        await asyncio.wait_for(lifecycle, timeout=30.0)
    finally:
        if not lifecycle.done():
            lifecycle.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await lifecycle

    row = await _read_row(tmp_path)
    assert row is not None
    assert row.daemon_pid is None


@pytest.mark.timeout(60)
async def test_migrations_run_off_the_event_loop_thread(tmp_path, monkeypatch) -> None:
    """T-ASYNC-4: Alembic's env.py calls asyncio.run; never on the loop thread."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    main_thread = threading.get_ident()
    observed_threads: list[int] = []
    real_run_migrations = daemon_run.run_migrations

    def spy(data_dir):
        observed_threads.append(threading.get_ident())
        return real_run_migrations(data_dir)

    monkeypatch.setattr(daemon_run, "run_migrations", spy)

    settings = load_settings()
    lifecycle = asyncio.create_task(run_daemon_lifecycle(settings))
    try:
        await _wait_for_row(tmp_path, lambda r: r.last_heartbeat_at is not None)
        await _set_stop_flag(tmp_path)
        await asyncio.wait_for(lifecycle, timeout=30.0)
    finally:
        if not lifecycle.done():
            lifecycle.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await lifecycle

    assert observed_threads, "lifecycle never invoked run_migrations"
    assert all(t != main_thread for t in observed_threads)


def _sqlite_write_heartbeat(data_dir: Path, age_seconds: float) -> None:
    """Direct DB write from outside the daemon (simulates a settled heartbeat)."""
    timestamp = (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat()
    connection = sqlite3.connect(data_dir / DB_FILE)
    try:
        connection.execute("INSERT OR IGNORE INTO daemon_state (id) VALUES (1)")
        connection.execute("UPDATE daemon_state SET last_heartbeat_at = ?", (timestamp,))
        connection.commit()
    finally:
        connection.close()


def _sqlite_insert_cookie(data_dir: Path, state: str) -> None:
    connection = sqlite3.connect(data_dir / DB_FILE)
    try:
        connection.execute(
            "INSERT INTO cookies (cookie_blob, validation_state) VALUES (?, ?)",
            (b"# Netscape HTTP Cookie File", state),
        )
        connection.commit()
    finally:
        connection.close()


_USAGE = namedtuple("usage", ("total", "used", "free"))


def _mock_disk_usage(free_percent: float, total: int = 1_000_000):
    """T-DEPLOY-21: controlled free percentage, never the real disk."""

    def fake(path):
        free = total * free_percent / 100
        return _USAGE(total=total, used=total - free, free=free)

    return fake


@pytest.mark.timeout(120)
def test_daemon_run_stop_real_subprocess(tmp_path) -> None:
    """13.2 mandatory case: `daemon stop` really stops the `daemon run` process (T-CLI-6)."""
    env = dict(os.environ, DATA_DIR=str(tmp_path), HEARTBEAT_INTERVAL_SECONDS="1")
    log_path = tmp_path / "daemon-run.log"
    with log_path.open("wb") as log_file:
        run_proc = subprocess.Popen(
            ["uv", "run", "tikdown-rs", "daemon", "run"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
        try:
            # Poll the DB with short deadlines; fail fast if the daemon died early.
            deadline = time.monotonic() + 90.0
            heartbeat_seen = False
            while time.monotonic() < deadline:
                if run_proc.poll() is not None:
                    raise AssertionError(
                        f"daemon run exited early with {run_proc.returncode}: "
                        f"{log_path.read_text(encoding='utf-8', errors='replace')}"
                    )
                connection = sqlite3.connect(tmp_path / DB_FILE, timeout=2.0)
                try:
                    rows = list(
                        connection.execute(
                            "SELECT last_heartbeat_at FROM daemon_state WHERE id = 1"
                        )
                    )
                except sqlite3.OperationalError:
                    rows = []
                finally:
                    connection.close()
                if rows and rows[0][0]:
                    heartbeat_seen = True
                    break
                time.sleep(POLL_STEP_SECONDS)
            assert heartbeat_seen, "heartbeat row never appeared before the deadline"

            stop_result = subprocess.run(
                ["uv", "run", "tikdown-rs", "daemon", "stop"],
                capture_output=True,
                check=False,
                env=env,
                timeout=60,
            )
            assert stop_result.returncode == 0, (
                f"daemon stop failed: stdout={stop_result.stdout!r} stderr={stop_result.stderr!r}"
            )

            exit_deadline = time.monotonic() + 20.0
            while run_proc.poll() is None and time.monotonic() < exit_deadline:
                time.sleep(POLL_STEP_SECONDS)
            assert run_proc.poll() is not None, (
                "daemon run still alive after daemon stop: "
                f"{log_path.read_text(encoding='utf-8', errors='replace')}"
            )
            assert run_proc.returncode == 0
        finally:
            if run_proc.poll() is None:
                run_proc.kill()
                run_proc.wait(timeout=10)


@pytest.mark.timeout(60)
def test_healthcheck_fresh_stale_and_no_row(tmp_path, monkeypatch) -> None:
    """Healthcheck: fresh -> 0; stale (> 3x interval) -> 1; no row -> 1 (10.1, 11.2)."""
    from typer.testing import CliRunner

    from tikdown_rs.cli.main import app

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    run_migrations(tmp_path)  # schema present; healthcheck itself never migrates
    runner = CliRunner()

    # No row yet.
    result = runner.invoke(app, ["daemon", "healthcheck"])
    assert result.exit_code == 1

    # §10.1 binary thresholds: fresh ALSO needs >= 1 valid cookie and disk ok.
    monkeypatch.setattr(shutil, "disk_usage", _mock_disk_usage(50.0))
    _sqlite_insert_cookie(tmp_path, "valid")

    # Fresh heartbeat (age 0 <= 3 x 10 s default interval) + valid cookie + disk ok.
    _sqlite_write_heartbeat(tmp_path, age_seconds=0.0)
    result = runner.invoke(app, ["daemon", "healthcheck"])
    assert result.exit_code == 0, _all_output(result)

    # Stale heartbeat: older than 3 x HEARTBEAT_INTERVAL_SECONDS (30 s).
    _sqlite_write_heartbeat(tmp_path, age_seconds=35.0)
    result = runner.invoke(app, ["daemon", "healthcheck"])
    assert result.exit_code == 1


@pytest.mark.timeout(60)
def test_daemon_status_prints_m0_keys_and_exit_codes(tmp_path, monkeypatch) -> None:
    """10.1 M0 subset: heartbeat age, monitor_running, daemon_pid; ASCII key: value."""
    from typer.testing import CliRunner

    from tikdown_rs.cli.main import app

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    runner = CliRunner()

    # No row: exit 1.
    result = runner.invoke(app, ["daemon", "status"])
    assert result.exit_code == 1

    # Running-looking row: exit 0 with the M0 keys.
    connection = sqlite3.connect(tmp_path / DB_FILE)
    try:
        connection.execute(
            "INSERT INTO daemon_state (id, daemon_pid, last_heartbeat_at, monitor_running) "
            "VALUES (1, 4321, ?, 1)",
            (datetime.now(UTC).isoformat(),),
        )
        connection.commit()
    finally:
        connection.close()

    result = runner.invoke(app, ["daemon", "status"])
    assert result.exit_code == 0, result.output
    assert "heartbeat_age_seconds:" in result.output
    assert "monitor_running: 1" in result.output
    assert "daemon_pid: 4321" in result.output
    # T-CLI-1: ASCII-only output.
    result.output.encode("ascii")


@pytest.mark.timeout(60)
def test_healthcheck_rejects_without_valid_cookie(tmp_path, monkeypatch) -> None:
    """§10.1 binary cookie threshold: zero VALID cookies -> unhealthy (strict)."""
    from typer.testing import CliRunner

    from tikdown_rs.cli.main import app

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(shutil, "disk_usage", _mock_disk_usage(50.0))
    run_migrations(tmp_path)
    _sqlite_write_heartbeat(tmp_path, age_seconds=0.0)
    _sqlite_insert_cookie(tmp_path, "invalid")
    _sqlite_insert_cookie(tmp_path, "inconclusive")

    result = CliRunner().invoke(app, ["daemon", "healthcheck"])
    assert result.exit_code == 1
    assert "cookie" in _all_output(result)


@pytest.mark.timeout(60)
def test_healthcheck_rejects_low_disk(tmp_path, monkeypatch) -> None:
    """§10.1 binary disk threshold: free <= threshold -> unhealthy (T-DEPLOY-21)."""
    from typer.testing import CliRunner

    from tikdown_rs.cli.main import app

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(shutil, "disk_usage", _mock_disk_usage(5.0))  # < 10% default
    run_migrations(tmp_path)
    _sqlite_write_heartbeat(tmp_path, age_seconds=0.0)
    _sqlite_insert_cookie(tmp_path, "valid")

    result = CliRunner().invoke(app, ["daemon", "healthcheck"])
    assert result.exit_code == 1
    assert "disk" in _all_output(result)


@pytest.mark.timeout(60)
def test_daemon_status_prints_cookies_and_selfcheck(tmp_path, monkeypatch) -> None:
    """10.1: status shows cookies by state and the last selfcheck result (ASCII)."""
    from typer.testing import CliRunner

    from tikdown_rs.cli.main import app

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    run_migrations(tmp_path)
    # T-DB-10 chain confirmation: the unreleased 0002 DDL ships degraded_reason.
    connection = sqlite3.connect(tmp_path / DB_FILE)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(daemon_state)")}
    finally:
        connection.close()
    assert "degraded_reason" in columns
    connection = sqlite3.connect(tmp_path / DB_FILE)
    try:
        connection.execute(
            "INSERT INTO daemon_state (id, daemon_pid, last_heartbeat_at, monitor_running,"
            " last_selfcheck_at, last_selfcheck_ok, degraded_reason) VALUES (1, 4321, ?, 1, ?, 0, ?)",
            (
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
                "impersonation: curl_cffi-missing",
            ),
        )
        for state in ("valid", "invalid", "invalid", "inconclusive"):
            connection.execute(
                "INSERT INTO cookies (cookie_blob, validation_state) VALUES (?, ?)",
                (b"# Netscape HTTP Cookie File", state),
            )
        connection.commit()
    finally:
        connection.close()

    result = CliRunner().invoke(app, ["daemon", "status"])
    assert result.exit_code == 0, _all_output(result)
    output = _all_output(result)
    assert "cookies_valid: 1" in output
    assert "cookies_invalid: 2" in output
    assert "cookies_inconclusive: 1" in output
    assert "last_selfcheck_ok: 0" in output
    assert "degraded_reason: impersonation: curl_cffi-missing" in output
    output.encode("ascii")  # T-CLI-1


async def _selfcheck_session_factory():
    engine = create_db_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, make_session_factory(engine)


async def test_run_selfcheck_all_ok_updates_daemon_state(tmp_path, monkeypatch) -> None:
    """All probes ok -> ok result and daemon_state updated (ok=True, reason NULL)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(verify_module, "probe_impersonation", lambda: (True, "private-api", 3))
    monkeypatch.setattr(shutil, "which", _fake_which())
    settings = load_settings()

    engine, factory = await _selfcheck_session_factory()
    try:
        result = await run_selfcheck(settings, factory)
        assert result.ok is True
        assert result.degraded_reason is None
        async with factory() as session:
            row = await session.get(DaemonState, 1)
        assert row.last_selfcheck_ok is True
        assert row.degraded_reason is None
        assert row.last_selfcheck_at is not None
    finally:
        await engine.dispose()


async def test_run_selfcheck_ffprobe_missing_degrades(tmp_path, monkeypatch) -> None:
    """T-DEPLOY-10: ffprobe is a hard binary dependency; missing -> degraded."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(verify_module, "probe_impersonation", lambda: (True, "private-api", 3))
    monkeypatch.setattr(shutil, "which", _fake_which({"ffprobe"}))
    settings = load_settings()

    engine, factory = await _selfcheck_session_factory()
    try:
        result = await run_selfcheck(settings, factory)
        assert result.ok is False
        assert "ffprobe" in result.degraded_reason
        async with factory() as session:
            row = await session.get(DaemonState, 1)
        assert row.last_selfcheck_ok is False
        assert "ffprobe" in row.degraded_reason
    finally:
        await engine.dispose()


async def test_run_selfcheck_impersonation_unavailable_degrades(tmp_path, monkeypatch) -> None:
    """4.1/T-ENGINE-22: no impersonation -> degraded with the probe cause."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        verify_module, "probe_impersonation", lambda: (False, "curl_cffi-missing", 0)
    )
    monkeypatch.setattr(shutil, "which", _fake_which())
    settings = load_settings()

    engine, factory = await _selfcheck_session_factory()
    try:
        result = await run_selfcheck(settings, factory)
        assert result.ok is False
        assert "impersonation" in result.degraded_reason
        assert "curl_cffi-missing" in result.degraded_reason
    finally:
        await engine.dispose()


async def test_run_selfcheck_data_dir_unwritable_degrades(tmp_path, monkeypatch) -> None:
    """Unwritable DATA_DIR -> degraded naming data_dir (no exception escape)."""
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")  # a FILE: mkdir/writes on it fail
    monkeypatch.setenv("DATA_DIR", str(blocker))
    monkeypatch.setattr(verify_module, "probe_impersonation", lambda: (True, "private-api", 3))
    monkeypatch.setattr(shutil, "which", _fake_which())
    settings = load_settings()

    engine, factory = await _selfcheck_session_factory()
    try:
        result = await run_selfcheck(settings, factory)
        assert result.ok is False
        assert "data_dir" in result.degraded_reason
    finally:
        await engine.dispose()


@pytest.mark.timeout(60)
def test_daemon_selfcheck_command_ok_and_degraded(tmp_path, monkeypatch) -> None:
    """CLI selfcheck: ASCII report, exit 0 ok / exit 1 degraded, state persisted."""
    from typer.testing import CliRunner

    from tikdown_rs.cli.main import app

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(verify_module, "probe_impersonation", lambda: (True, "private-api", 3))
    monkeypatch.setattr(shutil, "which", _fake_which())
    runner = CliRunner()

    result = runner.invoke(app, ["daemon", "selfcheck"])
    assert result.exit_code == 0, _all_output(result)
    output = _all_output(result)
    assert "impersonation:" in output
    assert "binaries:" in output
    assert "data_dir:" in output
    assert "overall: ok" in output
    output.encode("ascii")  # T-CLI-1
    connection = sqlite3.connect(tmp_path / DB_FILE)
    try:
        persisted = list(
            connection.execute(
                "SELECT last_selfcheck_ok, degraded_reason FROM daemon_state WHERE id = 1"
            )
        )
    finally:
        connection.close()
    assert persisted == [(1, None)]

    # Degraded run: ffprobe missing -> exit 1 with an actionable message.
    monkeypatch.setattr(shutil, "which", _fake_which({"ffprobe"}))
    result = runner.invoke(app, ["daemon", "selfcheck"])
    assert result.exit_code == 1
    assert "ffprobe" in _all_output(result)
    connection = sqlite3.connect(tmp_path / DB_FILE)
    try:
        persisted = list(
            connection.execute(
                "SELECT last_selfcheck_ok, degraded_reason FROM daemon_state WHERE id = 1"
            )
        )
    finally:
        connection.close()
    assert persisted[0][0] == 0
    assert "ffprobe" in persisted[0][1]

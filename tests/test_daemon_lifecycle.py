"""Daemon lifecycle tests: startup order, stop flag wiring, real subprocess smoke.

Deterministic: tmp_path DATA_DIR, real file DB, no network, heartbeat-only
scheduler. Traps covered: T-CLI-6, T-CLI-7, T-ASYNC-3, T-ASYNC-4, T-DEPLOY-8.
Rules: 5.1, 5.2, 11.2, 13.2 (daemon mandatory cases).
"""

import asyncio
import contextlib
import os
import sqlite3
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

import tikdown_rs.daemon.run as daemon_run
from tikdown_rs.core.config import load_settings
from tikdown_rs.core.daemon_state import read_status, set_stop_requested
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.daemon.run import run_daemon_lifecycle
from tikdown_rs.models.daemon_state import DaemonState

DB_FILE = "tikdown-rs.db"
POLL_STEP_SECONDS = 0.05


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

    # Fresh heartbeat (age 0 <= 3 x 10 s default interval).
    _sqlite_write_heartbeat(tmp_path, age_seconds=0.0)
    result = runner.invoke(app, ["daemon", "healthcheck"])
    assert result.exit_code == 0, result.output

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

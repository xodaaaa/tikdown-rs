"""core.disk probes and the disk pause state machine (8.2).

Trampas neutralizadas: T-DEPLOY-21 (shutil.disk_usage is MOCKED with a
controlled percentage; no test may depend on the host disk). Regla: §10.1,
§13.1, §8.2.
"""

import asyncio
import shutil
from collections import namedtuple
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.config import Settings
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.disk import check_disk_pause, free_percent, is_disk_ok
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.core.notifications.events import (
    EVENT_DISK_PAUSED,
    EVENT_DISK_RESUMED,
    EVENT_DISK_WARNING,
)
from tikdown_rs.models import DaemonState

Usage = namedtuple("usage", ("total", "used", "free"))


def _mock_disk_usage(free_percent: float, total: int = 1_000_000):
    def fake(path):
        free = total * free_percent / 100
        return Usage(total=total, used=total - free, free=free)

    return fake


def test_free_percent_returns_controlled_math(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "disk_usage", _mock_disk_usage(25.0))
    assert free_percent("anywhere") == pytest.approx(25.0)


def test_is_disk_ok_threshold_is_strictly_greater(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exactly at the threshold is NOT ok (§10.1: free > disk_warning_free_percent)."""
    monkeypatch.setattr(shutil, "disk_usage", _mock_disk_usage(10.0))
    assert not is_disk_ok("anywhere", 10)

    monkeypatch.setattr(shutil, "disk_usage", _mock_disk_usage(10.1))
    assert is_disk_ok("anywhere", 10)

    monkeypatch.setattr(shutil, "disk_usage", _mock_disk_usage(9.9))
    assert not is_disk_ok("anywhere", 10)


# --- check_disk_pause: the 8.2 pause state machine ---


@pytest.fixture
async def migrated_factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Session factory over a REAL migrated file database (T-DATA-1 pattern)."""
    await asyncio.to_thread(run_migrations, tmp_path)
    engine = create_db_engine(sqlite_url_for(tmp_path))
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


def disk_settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, disk_warning_free_percent=10)


async def paused_flag(factory: async_sessionmaker[AsyncSession]) -> bool | None:
    async with factory() as session:
        row = await session.get(DaemonState, 1)
    return bool(row.downloads_paused) if row is not None else None


async def test_below_threshold_pauses_once_and_dedupes(tmp_path: Path, migrated_factory) -> None:
    """Under threshold -> downloads_paused=1 + warning + paused events, ONCE per
    state change (not every 900 s check)."""
    events: list[dict] = []

    paused = await check_disk_pause(
        disk_settings(tmp_path),
        migrated_factory,
        on_event=events.append,
        disk_usage_fn=_mock_disk_usage(5.0),
    )

    assert paused is True
    assert await paused_flag(migrated_factory) is True
    assert [e["event"] for e in events] == [EVENT_DISK_WARNING, EVENT_DISK_PAUSED]
    assert events[0]["free_percent"] == pytest.approx(5.0)

    # Second consecutive low check: no new events, pause stays.
    events.clear()
    paused = await check_disk_pause(
        disk_settings(tmp_path),
        migrated_factory,
        on_event=events.append,
        disk_usage_fn=_mock_disk_usage(5.0),
    )
    assert paused is True
    assert await paused_flag(migrated_factory) is True
    assert events == []


async def test_space_recovered_auto_resumes(tmp_path: Path, migrated_factory) -> None:
    events: list[dict] = []

    await check_disk_pause(
        disk_settings(tmp_path), migrated_factory, disk_usage_fn=_mock_disk_usage(5.0)
    )
    paused = await check_disk_pause(
        disk_settings(tmp_path),
        migrated_factory,
        on_event=events.append,
        disk_usage_fn=_mock_disk_usage(50.0),
    )

    assert paused is False
    assert await paused_flag(migrated_factory) is False
    assert [e["event"] for e in events] == [EVENT_DISK_RESUMED]
    assert events[0]["free_percent"] == pytest.approx(50.0)


async def test_healthy_space_never_pauses_or_emits(tmp_path: Path, migrated_factory) -> None:
    events: list[dict] = []

    paused = await check_disk_pause(
        disk_settings(tmp_path),
        migrated_factory,
        on_event=events.append,
        disk_usage_fn=_mock_disk_usage(50.0),
    )

    assert paused is False
    assert await paused_flag(migrated_factory) is None  # no row written, no pause
    assert events == []


async def test_check_counts_the_backups_directory(tmp_path: Path, migrated_factory) -> None:
    """8.2: the check also accounts for <DATA_DIR>/backups (reported in the
    warning payload; retain-count bounded, so a plain size sum suffices)."""
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "db-0001.db").write_bytes(b"x" * 123)
    (backups / "db-0002.db").write_bytes(b"y" * 77)
    events: list[dict] = []

    await check_disk_pause(
        disk_settings(tmp_path),
        migrated_factory,
        on_event=events.append,
        disk_usage_fn=_mock_disk_usage(5.0),
    )

    assert events[0]["backups_bytes"] == 200


async def test_check_without_backups_directory_reports_zero(
    tmp_path: Path, migrated_factory
) -> None:
    events: list[dict] = []
    await check_disk_pause(
        disk_settings(tmp_path),
        migrated_factory,
        on_event=events.append,
        disk_usage_fn=_mock_disk_usage(5.0),
    )
    assert events[0]["backups_bytes"] == 0


# --- CLI: system disk [--resume] (10.1) ---


def _cli_runner():
    from typer.testing import CliRunner

    from tikdown_rs.cli.main import app

    return CliRunner(), app


async def paused_flag_migrated(tmp_path: Path) -> bool | None:
    """Read daemon_state.downloads_paused from the DB the CLI just used."""
    engine = create_db_engine(sqlite_url_for(tmp_path))
    try:
        async with make_session_factory(engine)() as session:
            row = await session.get(DaemonState, 1)
        return bool(row.downloads_paused) if row is not None else None
    finally:
        await engine.dispose()


def _factory_for(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Sync helper: a session factory over a migrated tmp_path DB."""
    run_migrations(tmp_path)
    engine = create_db_engine(sqlite_url_for(tmp_path))
    return make_session_factory(engine)


async def _seed_pause(tmp_path: Path, settings: Settings) -> None:
    """Run one low-space check to set downloads_paused=1 (DB already migrated)."""
    engine = create_db_engine(sqlite_url_for(tmp_path))
    try:
        await check_disk_pause(settings, make_session_factory(engine))
    finally:
        await engine.dispose()


def test_cli_disk_reports_pause_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, app = _cli_runner()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = Settings(data_dir=tmp_path, disk_warning_free_percent=10)

    # The pause flag is set by the periodic check (or ENOSPC), not by the CLI:
    # seed it via the real state machine, then observe it through the CLI.
    monkeypatch.setattr(shutil, "disk_usage", _mock_disk_usage(5.0))
    run_migrations(tmp_path)  # sync: env.py calls asyncio.run internally
    asyncio.run(_seed_pause(tmp_path, settings))

    result = runner.invoke(app, ["system", "disk"])
    assert result.exit_code == 0, result.output
    assert "free_percent: 5.0" in result.output
    assert "warning_threshold_percent: 10" in result.output
    assert "downloads_paused: 1" in result.output

    monkeypatch.setattr(shutil, "disk_usage", _mock_disk_usage(50.0))
    result = runner.invoke(app, ["system", "disk", "--resume"])
    assert result.exit_code == 0, result.output
    assert "downloads_paused: 0" in result.output
    assert asyncio.run(paused_flag_migrated(tmp_path)) is False


def test_cli_disk_resume_does_not_require_a_prior_daemon_state_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, app = _cli_runner()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(shutil, "disk_usage", _mock_disk_usage(50.0))
    result = runner.invoke(app, ["system", "disk", "--resume"])
    assert result.exit_code == 0, result.output
    assert "downloads_paused: 0" in result.output

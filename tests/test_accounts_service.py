"""Accounts service + CLI: add/list/pause/resume/notify/remove.

Trampas neutralizadas: T-BACKFILL-1 (last_check_at stays NULL), T-BACKFILL-9
(backfill_status starts 'idle'), T-CLI-4. Regla: 3.1, 10.1, 10.2.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tikdown_rs.cli.main import app
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.models import MonitoredAccount, Video
from tikdown_rs.services.accounts import (
    add_account,
    get_account,
    list_accounts,
    remove_account,
    set_notify,
    set_paused,
)

runner = CliRunner()


@pytest.fixture
async def migrated_factory(tmp_path: Path):
    """Engine + session factory over a REAL file DB migrated with run_migrations."""
    import asyncio

    await asyncio.to_thread(run_migrations, tmp_path)
    engine = create_db_engine(sqlite_url_for(tmp_path))
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


# --- add_account ---


async def test_add_normalizes_at_and_case(migrated_factory) -> None:
    account_id = await add_account(migrated_factory, "@SomeUser")
    async with migrated_factory() as session:
        row = await session.get(MonitoredAccount, account_id)
    assert row is not None
    assert row.username == "someuser"


async def test_add_rejects_empty_username(migrated_factory) -> None:
    with pytest.raises(ConfigurationError):
        await add_account(migrated_factory, "@@")


async def test_add_duplicate_raises_naming_the_user(migrated_factory) -> None:
    await add_account(migrated_factory, "@dup")
    with pytest.raises(ConfigurationError, match="dup"):
        await add_account(migrated_factory, "@DUP")


async def test_add_defaults_follow_3_1(migrated_factory) -> None:
    account_id = await add_account(migrated_factory, "@defaults")
    async with migrated_factory() as session:
        row = await session.get(MonitoredAccount, account_id)
    assert row is not None
    assert row.mode == "history"
    assert row.backfill_status == "idle"  # T-BACKFILL-9
    assert row.last_check_at is None  # T-BACKFILL-1: never initialized to now
    assert row.paused is False
    assert row.needs_review is False
    assert row.notify_on_download is False
    assert row.monitor_after_backfill is False
    assert row.created_at is not None
    assert row.updated_at is not None


async def test_add_then_monitor_sets_flag(migrated_factory) -> None:
    account_id = await add_account(migrated_factory, "@later", mode="monitor", then_monitor=True)
    async with migrated_factory() as session:
        row = await session.get(MonitoredAccount, account_id)
    assert row is not None
    assert row.mode == "monitor"
    assert row.monitor_after_backfill is True


async def test_add_rejects_unknown_mode(migrated_factory) -> None:
    with pytest.raises(ConfigurationError):
        await add_account(migrated_factory, "@weird", mode="bogus")


# --- list_accounts ---


async def test_list_returns_declared_fields(migrated_factory) -> None:
    await add_account(migrated_factory, "@alpha", mode="monitor", then_monitor=True)
    await add_account(migrated_factory, "@beta")
    await set_notify(migrated_factory, "alpha", True)
    rows = await list_accounts(migrated_factory)
    assert len(rows) == 2
    first = rows[0]
    assert first["username"] == "alpha"
    assert first["mode"] == "monitor"
    assert first["paused"] is False
    assert first["needs_review"] is False
    assert first["backfill_status"] == "idle"
    assert first["monitor_after_backfill"] is True
    assert first["notify_on_download"] is True
    assert "id" in first


async def test_list_empty(migrated_factory) -> None:
    assert await list_accounts(migrated_factory) == []


# --- set_paused / set_notify ---


async def test_set_paused_persists_across_sessions(migrated_factory) -> None:
    await add_account(migrated_factory, "@pauser")
    await set_paused(migrated_factory, "@Pauser", True)
    async with migrated_factory() as session:
        row = await session.get(MonitoredAccount, 1)
    assert row is not None
    assert row.paused is True


async def test_set_notify_persists_across_sessions(migrated_factory) -> None:
    await add_account(migrated_factory, "@notified")
    await set_notify(migrated_factory, "@Notified", True)
    async with migrated_factory() as session:
        row = await session.get(MonitoredAccount, 1)
    assert row is not None
    assert row.notify_on_download is True


async def test_operations_on_unknown_user_raise(migrated_factory) -> None:
    assert await get_account(migrated_factory, "@ghost") is None
    with pytest.raises(ConfigurationError, match="ghost"):
        await set_paused(migrated_factory, "@ghost", True)
    with pytest.raises(ConfigurationError, match="ghost"):
        await set_notify(migrated_factory, "@ghost", True)
    with pytest.raises(ConfigurationError, match="ghost"):
        await remove_account(migrated_factory, "@ghost")


# --- remove_account ---


async def test_remove_without_videos_deletes_row(migrated_factory) -> None:
    await add_account(migrated_factory, "@removable")
    await remove_account(migrated_factory, "@Removable")
    assert await list_accounts(migrated_factory) == []


async def test_remove_with_videos_raises_and_keeps_account(migrated_factory) -> None:
    account_id = await add_account(migrated_factory, "@owner")
    async with migrated_factory() as session:
        session.add(
            Video(
                tiktok_video_id="7300000000000000000",
                account_id=account_id,
                status="pending",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
        await session.commit()

    with pytest.raises(ConfigurationError, match="videos"):
        await remove_account(migrated_factory, "@owner")

    async with migrated_factory() as session:
        row = await session.get(MonitoredAccount, account_id)
    assert row is not None  # no silent cascade


# --- CLI (tmp DATA_DIR + migrated DB, per invocation) ---


def test_cli_accounts_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    result = runner.invoke(app, ["accounts", "add", "@user1"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app, ["accounts", "add", "@user2", "--mode", "monitor", "--then-monitor"]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["accounts", "list"])
    assert result.exit_code == 0, result.output
    assert "user1" in result.output
    assert "user2" in result.output

    result = runner.invoke(app, ["accounts", "pause", "@user1"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["accounts", "resume", "@user1"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["accounts", "notify", "--on"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["accounts", "notify", "@user2", "--off"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["accounts", "remove", "@user2"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["accounts", "list"])
    assert result.exit_code == 0, result.output
    assert "user2" not in result.output


def test_cli_accounts_add_duplicate_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert runner.invoke(app, ["accounts", "add", "@dupe"]).exit_code == 0
    result = runner.invoke(app, ["accounts", "add", "@dupe"])
    assert result.exit_code == 1
    assert "ERROR" in result.output


def test_cli_accounts_check_still_loud_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["accounts", "check", "@user1"])
    assert result.exit_code == 1
    assert "ERROR" in result.output
    assert "not implemented" in result.output

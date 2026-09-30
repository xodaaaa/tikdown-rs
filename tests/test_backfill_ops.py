"""services/backfill_ops + cli/backfill: queue/cancel/retry-failed/status ops (9.4, 9.6, 10.1).

Trampas covered: T-BACKFILL-19, T-BACKFILL-12, T-ENGINE-18, T-CLI-4.
Reglas: 9.3, 9.4, 9.6, 10.1, 10.2.

Deterministic doubles only: migrated real file SQLite, CliRunner with a tmp
DATA_DIR, a monkeypatched run_backfill seam for the foreground path. No
network, no yt-dlp downloads.
"""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from typer.testing import CliRunner

import tikdown_rs.services.backfill as backfill_module
from tikdown_rs.cli.main import app
from tikdown_rs.core.archive import DownloadArchive
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.models import Cookie, MonitoredAccount, Video
from tikdown_rs.services.backfill_ops import (
    backfill_status_view,
    cancel_backfill,
    queue_backfill,
    retry_failed,
)

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


async def add_account(
    factory: async_sessionmaker[AsyncSession],
    username: str = "acct",
    backfill_status: str = "idle",
    **overrides,
) -> int:
    async with factory() as session:
        account = MonitoredAccount(username=username, backfill_status=backfill_status, **overrides)
        session.add(account)
        await session.commit()
        return account.id


async def get_account_row(factory, account_id: int) -> MonitoredAccount:
    async with factory() as session:
        return await session.get(MonitoredAccount, account_id)


async def add_failed_video(
    factory: async_sessionmaker[AsyncSession],
    account_id: int,
    video_id: str,
    error_category: str | None,
    retry_count: int = 0,
) -> int:
    async with factory() as session:
        video = Video(
            tiktok_video_id=video_id,
            account_id=account_id,
            url=f"https://www.tiktok.com/@acct/video/{video_id}",
            status="failed",
            error_category=error_category,
            error_message="boom",
            retry_count=retry_count,
        )
        session.add(video)
        await session.commit()
        return video.id


async def get_video(factory, video_row_id: int) -> Video:
    async with factory() as session:
        return await session.get(Video, video_row_id)


def make_archive(tmp_path: Path) -> DownloadArchive:
    return DownloadArchive(tmp_path / "download_archive.txt")


# --- queue_backfill (T-BACKFILL-19) ---


async def test_queue_from_idle(factory) -> None:
    account_id = await add_account(factory, backfill_status="idle")
    assert await queue_backfill(factory, "@acct", queue=True) == "queued"
    assert (await get_account_row(factory, account_id)).backfill_status == "queued"


async def test_queue_from_completed_with_queue_flag(factory) -> None:
    account_id = await add_account(factory, backfill_status="completed")
    assert await queue_backfill(factory, "acct", queue=True) == "queued"
    assert (await get_account_row(factory, account_id)).backfill_status == "queued"


async def test_queue_rejects_backfilling(factory) -> None:
    await add_account(factory, backfill_status="backfilling")
    with pytest.raises(ConfigurationError, match="backfilling"):
        await queue_backfill(factory, "acct", queue=True)


async def test_foreground_also_rejects_backfilling(factory) -> None:
    await add_account(factory, backfill_status="backfilling")
    with pytest.raises(ConfigurationError, match="backfilling"):
        await queue_backfill(factory, "acct", queue=False)


async def test_queue_from_cancelled_clears_pause_reason(factory) -> None:
    account_id = await add_account(factory, backfill_status="paused", backfill_pause_reason="disk")
    assert await queue_backfill(factory, "acct", queue=True) == "queued"
    row = await get_account_row(factory, account_id)
    assert row.backfill_status == "queued"
    assert row.backfill_pause_reason is None


async def test_queue_when_already_queued_is_noop(factory) -> None:
    account_id = await add_account(factory, backfill_status="queued")
    assert await queue_backfill(factory, "acct", queue=True) == "queued"
    assert (await get_account_row(factory, account_id)).backfill_status == "queued"


async def test_queue_unknown_user(factory) -> None:
    with pytest.raises(ConfigurationError, match="nobody"):
        await queue_backfill(factory, "nobody", queue=True)


# --- cancel_backfill (9.4) ---


async def test_cancel_backfilling_clears_pause_reason(factory) -> None:
    account_id = await add_account(
        factory, backfill_status="backfilling", backfill_pause_reason="network"
    )
    assert await cancel_backfill(factory, "acct") == "cancelled"
    row = await get_account_row(factory, account_id)
    assert row.backfill_status == "cancelled"
    assert row.backfill_pause_reason is None


async def test_cancel_queued(factory) -> None:
    account_id = await add_account(factory, backfill_status="queued")
    assert await cancel_backfill(factory, "@acct") == "cancelled"
    assert (await get_account_row(factory, account_id)).backfill_status == "cancelled"


async def test_cancel_completed_is_configuration_error(factory) -> None:
    await add_account(factory, backfill_status="completed")
    with pytest.raises(ConfigurationError, match="completed"):
        await cancel_backfill(factory, "acct")


async def test_cancel_failed_is_configuration_error(factory) -> None:
    await add_account(factory, backfill_status="failed")
    with pytest.raises(ConfigurationError, match="failed"):
        await cancel_backfill(factory, "acct")


async def test_cancel_unknown_user(factory) -> None:
    with pytest.raises(ConfigurationError, match="nobody"):
        await cancel_backfill(factory, "nobody")


# --- retry_failed (T-ENGINE-18) ---


async def test_retry_failed_resets_transient_and_integrity_only(factory, tmp_path) -> None:
    account_id = await add_account(factory, backfill_status="completed")
    t = await add_failed_video(factory, account_id, "v1", "transient", retry_count=2)
    i = await add_failed_video(factory, account_id, "v2", "integrity")
    d = await add_failed_video(factory, account_id, "v3", "definitive")
    archive = make_archive(tmp_path)
    await archive.add("v2", account_id)  # T-ENGINE-18: the entry to discard

    count = await retry_failed(factory, "acct", False, archive=archive)
    assert count == 2

    assert (await get_video(factory, t)).status == "pending"
    assert (await get_video(factory, t)).retry_count == 2  # historical, kept
    assert (await get_video(factory, i)).status == "pending"
    assert (await get_video(factory, d)).status == "failed"  # permanent
    assert (await get_video(factory, d)).error_category == "definitive"
    assert (tmp_path / "download_archive.txt").read_text(encoding="utf-8") == ""  # discarded


async def test_retry_failed_all_accounts(factory, tmp_path) -> None:
    a1 = await add_account(factory, username="acct1", backfill_status="completed")
    a2 = await add_account(factory, username="acct2", backfill_status="failed")
    await add_failed_video(factory, a1, "w1", "transient")
    await add_failed_video(factory, a2, "w2", "integrity")
    archive = make_archive(tmp_path)
    count = await retry_failed(factory, None, True, archive=archive)
    assert count == 2


async def test_retry_failed_neither_user_nor_all(factory, tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="--all"):
        await retry_failed(factory, None, False, archive=make_archive(tmp_path))


async def test_retry_failed_both_user_and_all(factory, tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="--all"):
        await retry_failed(factory, "acct", True, archive=make_archive(tmp_path))


async def test_retry_failed_unknown_user(factory, tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="nobody"):
        await retry_failed(factory, "nobody", False, archive=make_archive(tmp_path))


# --- backfill_status_view (9.3) ---


async def test_status_view_fields(factory) -> None:
    await add_account(
        factory,
        backfill_status="paused",
        backfill_total=10,
        backfill_done=4,
        backfill_cursor="20260101",
        backfill_pause_reason="disk",
        needs_review=True,
    )
    view = await backfill_status_view(factory, "acct")
    assert view["username"] == "acct"
    assert view["backfill_status"] == "paused"
    assert view["backfill_total"] == 10
    assert view["backfill_done"] == 4
    assert view["backfill_cursor"] == "20260101"
    assert view["pause_reason"] == "disk"
    assert view["needs_review"] == 1


async def test_status_view_unknown_user(factory) -> None:
    with pytest.raises(ConfigurationError, match="nobody"):
        await backfill_status_view(factory, "nobody")


# --- CLI (10.1, T-CLI-4) ---


def _invoke(*argv: str):
    return runner.invoke(app, ["backfill", *argv])


def test_cli_status_happy_path(data_dir, factory) -> None:
    asyncio.run(
        add_account(factory, backfill_status="completed", backfill_total=7, backfill_done=7)
    )
    result = _invoke("status", "@acct")
    assert result.exit_code == 0, result.output
    assert "backfill_status: completed" in result.output
    assert "backfill_total: 7" in result.output
    assert "backfill_done: 7" in result.output


def test_cli_cancel_happy_path(data_dir, factory) -> None:
    asyncio.run(add_account(factory, backfill_status="backfilling"))
    result = _invoke("cancel", "@acct")
    assert result.exit_code == 0, result.output
    assert "cancelled" in result.output


def test_cli_cancel_terminal_is_error(data_dir, factory) -> None:
    asyncio.run(add_account(factory, backfill_status="completed"))
    result = _invoke("cancel", "@acct")
    assert result.exit_code == 1
    assert "ERROR" in result.output


def test_cli_run_queue_happy_path(data_dir, factory) -> None:
    asyncio.run(add_account(factory, backfill_status="idle"))
    result = _invoke("run", "@acct", "--queue")
    assert result.exit_code == 0, result.output
    assert "queued" in result.output


def test_cli_run_queue_twice_on_backfilling_is_error(data_dir, factory) -> None:
    asyncio.run(add_account(factory, backfill_status="backfilling"))
    result = _invoke("run", "@acct", "--queue")
    assert result.exit_code == 1
    assert "ERROR" in result.output


def test_cli_retry_failed_happy_path(data_dir, factory) -> None:
    async def seed():
        account_id = await add_account(factory, backfill_status="completed")
        await add_failed_video(factory, account_id, "c1", "transient")

    asyncio.run(seed())
    result = _invoke("retry-failed", "@acct")
    assert result.exit_code == 0, result.output
    assert "1" in result.output


def test_cli_retry_failed_without_args_is_error(data_dir, factory) -> None:
    result = _invoke("retry-failed")
    assert result.exit_code == 1
    assert "ERROR" in result.output


def test_cli_foreground_run_wires_real_components(data_dir, factory, monkeypatch) -> None:
    """T-BACKFILL-12 regression: the engine receives a NON-EMPTY cookies blob."""
    from tikdown_rs.core.download_engine import YtDlpEngine

    async def seed():
        async with factory() as session:
            session.add(
                Cookie(label="c", cookie_blob=b"netscape-cookie-blob", validation_state="valid")
            )
            await session.commit()
        return await add_account(factory, backfill_status="idle")

    account_id = asyncio.run(seed())

    calls: dict = {}

    async def fake_run_backfill(session_factory, acct_id, **kwargs):
        calls["account_id"] = acct_id
        calls["kwargs"] = kwargs
        return "completed"

    monkeypatch.setattr(backfill_module, "run_backfill", fake_run_backfill)

    result = _invoke("run", "@acct")
    assert result.exit_code == 0, result.output
    assert calls["account_id"] == account_id
    kwargs = calls["kwargs"]
    assert isinstance(kwargs["engine"], YtDlpEngine)
    assert kwargs["engine"]._cookies_blob == b"netscape-cookie-blob"  # never []
    assert kwargs["pacer"] is not None
    assert kwargs["semaphore"] is not None
    assert isinstance(kwargs["archive"], DownloadArchive)
    assert "completed" in result.output

    async def read_status() -> str:
        async with factory() as session:
            return (await session.get(MonitoredAccount, account_id)).backfill_status

    # queue_backfill put it 'queued' before the (faked) run picked it up.
    assert asyncio.run(read_status()) == "queued"


def test_cli_foreground_run_without_cookies_is_error(data_dir, factory) -> None:
    asyncio.run(add_account(factory, backfill_status="idle"))
    result = _invoke("run", "@acct")
    assert result.exit_code == 1
    assert "ERROR" in result.output
    assert "no_cookies" in result.output

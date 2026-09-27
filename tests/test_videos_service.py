"""services/videos.handle_download_result: the single post-download truth point.

Trampas covered: T-ENGINE-5, T-ENGINE-18, T-ENGINE-24, T-ASYNC-8, T-ASYNC-14/15,
T-BACKFILL-13/15/21, T-DATA-1, T-DATA-3, T-DATA-10. Reglas: 3.3, 3.1, 4.4, 4.7.

All doubles are deterministic: tmp_path files, injected sync ffprobe/sha fakes,
a migrated real SQLite database (T-DATA-1: the pending INSERT passes the real
CHECK). The production caller (monitor/backfill jobs) is pending by plan order
(M3/M4); these tests pin the service contract.
"""

import asyncio
import csv
import errno
import hashlib
import io
import json
import os
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from typer.testing import CliRunner

from tikdown_rs.core.archive import DownloadArchive
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.errors import ConfigurationError, DownloadTimeoutError
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.core.notifications.events import EVENT_DISK_PAUSED, EVENT_DOWNLOAD_FAILED
from tikdown_rs.models import DaemonState, MonitoredAccount, Video
from tikdown_rs.services.videos import (
    HandleResult,
    handle_download_result,
    persist_download_failure,
)

GOOD_PROBE = {
    "streams": [{"codec_name": "h264", "width": 1280, "height": 720}],
    "format": {"duration": "3.5"},
}
NO_VIDEO_PROBE = {"streams": [], "format": {"duration": "0"}}

VIDEO_BYTES = b"fake tiktok video payload for sha256"
VIDEO_SHA256 = hashlib.sha256(VIDEO_BYTES).hexdigest()


@pytest.fixture
async def migrated_factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Session factory over a REAL migrated file database (T-DATA-1 pattern)."""
    await asyncio.to_thread(run_migrations, tmp_path)
    engine = create_db_engine(sqlite_url_for(tmp_path))
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


async def add_account(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        account = MonitoredAccount(username="acct", backfill_status="idle")
        session.add(account)
        await session.commit()
        return account.id


async def add_pending_video(
    factory: async_sessionmaker[AsyncSession], account_id: int, video_id: str = "123"
) -> int:
    async with factory() as session:
        video = Video(tiktok_video_id=video_id, account_id=account_id, status="pending")
        session.add(video)
        await session.commit()
        return video.id


def probe_fn(result: dict):
    """Sync injected ffprobe fake returning a fresh copy of ``result``."""

    def ffprobe(path: Path) -> dict:
        return json.loads(json.dumps(result))

    return ffprobe


def probing_twice(first: dict, second: dict):
    """ffprobe fake returning ``first`` on the first call, ``second`` after."""
    calls = []

    def ffprobe(path: Path) -> dict:
        calls.append(path)
        return json.loads(json.dumps(first if len(calls) == 1 else second))

    return ffprobe


class Recorder:
    """Sync event channel recorder (T-BACKFILL-15: the channel is synchronous)."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(event)


class FakeArchive:
    """Order-recording archive double around the real file semantics."""

    def __init__(self) -> None:
        self.order: list[str] = []
        self.removed: list[str] = []

    async def remove(self, video_id: str) -> bool:
        self.order.append(f"remove:{video_id}")
        self.removed.append(video_id)
        return True


# --- integrity OK: downloaded persistence (steps 1-4 of 4.7) ---


async def test_integrity_ok_persists_downloaded_with_all_fields(
    tmp_path: Path, migrated_factory
) -> None:
    account_id = await add_account(migrated_factory)
    row_id = await add_pending_video(migrated_factory, account_id)
    downloaded = tmp_path / "videos" / "acct" / "123.retry-1.mp4"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(VIDEO_BYTES)
    events = Recorder()

    result = await handle_download_result(
        migrated_factory,
        row_id,
        downloaded,
        "123",
        account_id,
        base_retry_count=1,
        ffprobe_fn=probe_fn(GOOD_PROBE),
        on_event=events,
    )

    assert isinstance(result, HandleResult)
    assert (result.outcome, result.error_category) == ("downloaded", None)
    final = Path(result.file_path)
    assert final.name == "123.mp4"  # T-ASYNC-15: renamed, no .retry-N suffix
    assert final.read_bytes() == VIDEO_BYTES
    assert not downloaded.exists()
    assert final.is_absolute()  # T-BACKFILL-21

    async with migrated_factory() as session:
        video = await session.get(Video, row_id)
        account = await session.get(MonitoredAccount, account_id)  # second session read
    assert video.status == "downloaded"
    assert video.file_hash == VIDEO_SHA256  # step 2: real SHA-256 of the bytes
    assert video.file_size == len(VIDEO_BYTES)
    assert video.retry_count == 1  # base_retry_count + 0 extra attempts
    assert video.error_category is None
    assert video.error_message is None
    assert video.downloaded_at is not None
    assert account.total_disk_bytes == len(VIDEO_BYTES)  # T-DATA-10, same commit


async def test_rename_happens_before_persistence_commit(
    tmp_path: Path, migrated_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-ASYNC-15: the .retry-N file is renamed ONLY after integrity passes,
    and the rename lands BEFORE the single persistence commit."""
    account_id = await add_account(migrated_factory)
    row_id = await add_pending_video(migrated_factory, account_id)
    downloaded = tmp_path / "videos" / "acct" / "123.retry-1.mp4"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(VIDEO_BYTES)

    order: list[str] = []
    original_commit = AsyncSession.commit

    async def spy_commit(self: AsyncSession) -> None:
        order.append("commit")
        await original_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", spy_commit)
    real_replace = os.replace

    def spy_rename(src: Path, dst: Path) -> None:
        order.append("rename")
        real_replace(src, dst)  # still perform the actual rename

    monkeypatch.setattr("tikdown_rs.services.videos._rename_sync", spy_rename)

    await handle_download_result(
        migrated_factory, row_id, downloaded, "123", account_id, ffprobe_fn=probe_fn(GOOD_PROBE)
    )

    assert order == ["rename", "commit"]


# --- step 1 failures: missing or zero-byte file ---


@pytest.mark.parametrize("missing", [True, False])
async def test_step1_missing_or_empty_file_is_integrity_failed(
    tmp_path: Path, migrated_factory, missing: bool
) -> None:
    account_id = await add_account(migrated_factory)
    row_id = await add_pending_video(migrated_factory, account_id)
    downloaded = tmp_path / "videos" / "acct" / "123.mp4"
    if not missing:
        downloaded.parent.mkdir(parents=True)
        downloaded.write_bytes(b"")  # zero-byte: also a step 1 failure
    events = Recorder()

    result = await handle_download_result(
        migrated_factory, row_id, downloaded, "123", account_id, on_event=events
    )

    assert (result.outcome, result.error_category) == ("failed", "integrity")
    async with migrated_factory() as session:
        video = await session.get(Video, row_id)
    assert video.status == "failed"  # 3.3: 'integrity' persists as failed
    assert video.error_category == "integrity"
    assert events.events[-1]["status"] == "failed"


# --- T-ENGINE-5 cause 1: slideshow (expected, no retries) ---


async def test_slideshow_is_skipped_archived_and_never_retried(
    tmp_path: Path, migrated_factory
) -> None:
    account_id = await add_account(migrated_factory)
    row_id = await add_pending_video(migrated_factory, account_id, "slide1")
    downloaded = tmp_path / "videos" / "acct" / "slide1.mp4"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(VIDEO_BYTES)
    archive = DownloadArchive(tmp_path / "download_archive.txt", migrated_factory)
    events = Recorder()

    result = await handle_download_result(
        migrated_factory,
        row_id,
        downloaded,
        "slide1",
        account_id,
        expected_has_video=False,
        ffprobe_fn=probe_fn(NO_VIDEO_PROBE),
        archive=archive,
        on_event=events,
    )

    assert (result.outcome, result.error_category) == ("skipped", None)
    assert await archive.contains("slide1")  # dedupe entry ADDED
    async with migrated_factory() as session:
        video = await session.get(Video, row_id)
    assert video.status == "skipped"
    assert video.error_category is None
    assert [e["status"] for e in events.events] == ["skipped"]


# --- T-ENGINE-5 cause 2: degraded response (real failure) ---


async def test_degraded_discards_archive_entry_and_fails_integrity(
    tmp_path: Path, migrated_factory
) -> None:
    """DR-18: the §4.7 fallback retry lives INSIDE engine.download (the funnel
    already discarded the archive entry before its fallback). The truth point
    keeps its own T-ENGINE-18 duty (discard the entry so retry-failed can
    re-download) and persists failed/integrity — no caller-side retry hook."""
    account_id = await add_account(migrated_factory)
    row_id = await add_pending_video(migrated_factory, account_id)
    downloaded = tmp_path / "videos" / "acct" / "123.mp4"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"audio only")
    archive = DownloadArchive(tmp_path / "download_archive.txt", migrated_factory)
    await archive.add("123")
    events = Recorder()

    result = await handle_download_result(
        migrated_factory,
        row_id,
        downloaded,
        "123",
        account_id,
        ffprobe_fn=probe_fn(NO_VIDEO_PROBE),
        archive=archive,
        on_event=events,
    )

    assert (result.outcome, result.error_category) == ("failed", "integrity")
    assert not await archive.contains("123")  # entry discarded for retry-failed
    async with migrated_factory() as session:
        video = await session.get(Video, row_id)
    assert video.status == "failed"
    assert video.error_category == "integrity"
    assert [e["status"] for e in events.events] == ["failed"]


async def test_degraded_without_archive_entry_still_fails_integrity(
    tmp_path: Path, migrated_factory
) -> None:
    account_id = await add_account(migrated_factory)
    row_id = await add_pending_video(migrated_factory, account_id)
    downloaded = tmp_path / "videos" / "acct" / "123.mp4"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"audio only")

    result = await handle_download_result(
        migrated_factory,
        row_id,
        downloaded,
        "123",
        account_id,
        ffprobe_fn=probe_fn(NO_VIDEO_PROBE),
    )

    assert (result.outcome, result.error_category) == ("failed", "integrity")


# --- T-DATA-3: download failures classified by THE classifier ---


@pytest.mark.parametrize(
    ("exc", "expected_category"),
    [
        (
            Exception("Requested content is not available, this video is requiring login"),
            "definitive",
        ),
        (Exception("HTTP Error 403: Forbidden"), "transient"),
        (DownloadTimeoutError("download of 123 exceeded download_timeout_seconds=60"), "transient"),
    ],
)
async def test_persist_download_failure_uses_the_classifier(
    migrated_factory, exc: Exception, expected_category: str
) -> None:
    account_id = await add_account(migrated_factory)
    row_id = await add_pending_video(migrated_factory, account_id, "dlfail")

    result = await persist_download_failure(migrated_factory, row_id, exc, base_retry_count=3)

    assert (result.outcome, result.error_category) == ("failed", expected_category)
    async with migrated_factory() as session:
        video = await session.get(Video, row_id)
    assert video.status == "failed"
    assert video.error_category == expected_category
    assert (
        "login" in video.error_message
        or "403" in video.error_message
        or "timeout" in video.error_message
    )
    assert video.retry_count == 3


# --- event channel contract (T-BACKFILL-15/13) ---


async def test_on_event_is_called_synchronously_and_is_optional(
    tmp_path: Path, migrated_factory
) -> None:
    account_id = await add_account(migrated_factory)
    row_id = await add_pending_video(migrated_factory, account_id)
    downloaded = tmp_path / "videos" / "acct" / "123.mp4"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(VIDEO_BYTES)
    seen: list[dict] = []

    def sync_recorder(event: dict) -> None:  # NOT async: a sync call, T-BACKFILL-15
        seen.append(event)

    await handle_download_result(
        migrated_factory,
        row_id,
        downloaded,
        "123",
        account_id,
        ffprobe_fn=probe_fn(GOOD_PROBE),
        on_event=sync_recorder,
        notify_on_download=True,
    )

    assert len(seen) == 1
    assert seen[0]["status"] == "downloaded"
    assert seen[0]["video_id"] == "123"
    assert seen[0]["notify_on_download"] is True  # T-BACKFILL-14: propagated

    # Without on_event nothing breaks.
    row_id2 = await add_pending_video(migrated_factory, account_id, "456")
    result = await handle_download_result(
        migrated_factory,
        row_id2,
        downloaded,
        "456",
        account_id,
        ffprobe_fn=probe_fn(GOOD_PROBE),
    )
    assert result.outcome == "downloaded"


# --- row existence contract (3.3) ---


async def test_unknown_video_row_id_is_configuration_error(migrated_factory) -> None:
    account_id = await add_account(migrated_factory)
    with pytest.raises(ConfigurationError):
        await handle_download_result(
            migrated_factory, 999999, Path("nowhere.mp4"), "123", account_id
        )
    with pytest.raises(ConfigurationError):
        await persist_download_failure(migrated_factory, 999999, Exception("boom"))


# --- T-ENGINE-27: ENOSPC is a LOCAL actionable failure (8.2) ---


async def test_enospc_local_failure_pauses_downloads_never_breaker_or_cookies(
    migrated_factory,
) -> None:
    """A 'local' (disk-full) failure pauses downloads and emits disk.paused;
    it never counts for the circuit breaker nor touches cookies (T-ENGINE-27).
    Per the M2 CHECK mapping, 'local' is not a storable category: the video row
    keeps error_category NULL with the message preserved."""
    account_id = await add_account(migrated_factory)
    row_id = await add_pending_video(migrated_factory, account_id, "enospc")
    events = Recorder()
    exc = OSError(errno.ENOSPC, "No space left on device")

    result = await persist_download_failure(
        migrated_factory, row_id, exc, base_retry_count=2, on_event=events
    )

    assert (result.outcome, result.error_category) == ("failed", None)
    async with migrated_factory() as session:
        video = await session.get(Video, row_id)
        state = await session.get(DaemonState, 1)
    assert video.status == "failed"
    assert video.error_category is None
    assert "No space left" in video.error_message
    assert video.retry_count == 2  # a network/disk failure consumes NO retries
    assert state.downloads_paused is True
    paused = [e for e in events.events if e["event"] == EVENT_DISK_PAUSED]
    failed = [e for e in events.events if e["event"] == EVENT_DOWNLOAD_FAILED]
    assert len(paused) == 1  # exactly one pause alert per state change
    assert len(failed) == 1  # the terminal event still fires exactly once


# --- M6 T2/T3: read-only listing + export (10.1, T-CLI-1/3, T-DEPLOY-16) ---

from tikdown_rs.cli.main import app as cli_app
from tikdown_rs.services.videos import export_videos, recent_videos

cli_runner = CliRunner()


async def seed_video(
    factory: async_sessionmaker[AsyncSession],
    account_id: int | None,
    video_id: str,
    *,
    status: str = "downloaded",
    **fields,
) -> int:
    async with factory() as session:
        row = Video(tiktok_video_id=video_id, account_id=account_id, status=status, **fields)
        session.add(row)
        await session.commit()
        return row.id


async def seed_two_accounts(factory) -> tuple[int, int]:
    async with factory() as session:
        first = MonitoredAccount(username="acct", backfill_status="idle")
        second = MonitoredAccount(username="other", backfill_status="idle")
        session.add_all([first, second])
        await session.commit()
        return first.id, second.id


DOWNLOAD_SPEC = {
    "downloaded_at": "2026-02-01T10:00:00+00:00",
    "local_path": "/data/videos/acct/1.mp4",
    "file_size": 123,
    "title": "title one",
    "description": "desc one",
}


class TestRecentVideos:
    async def test_orders_most_recent_first_by_downloaded_at(self, migrated_factory) -> None:
        account_id = await add_account(migrated_factory)
        await seed_video(
            migrated_factory, account_id, "1", downloaded_at="2026-01-01T00:00:00+00:00"
        )
        await seed_video(
            migrated_factory, account_id, "2", downloaded_at="2026-03-01T00:00:00+00:00"
        )
        await seed_video(
            migrated_factory, account_id, "3", downloaded_at="2026-02-01T00:00:00+00:00"
        )

        rows = await recent_videos(migrated_factory)

        assert [r["tiktok_video_id"] for r in rows] == ["2", "3", "1"]

    async def test_pending_rows_are_excluded_and_null_downloaded_at_sorts_last(
        self, migrated_factory
    ) -> None:
        account_id = await add_account(migrated_factory)
        await seed_video(migrated_factory, account_id, "pend", status="pending")
        await seed_video(migrated_factory, account_id, "fail", status="failed")
        await seed_video(migrated_factory, account_id, "ok")

        rows = await recent_videos(migrated_factory)

        assert [r["tiktok_video_id"] for r in rows] == ["ok", "fail"]

    async def test_limit_clamped_to_bounded_range(self, migrated_factory) -> None:
        account_id = await add_account(migrated_factory)
        for i in range(105):
            await seed_video(migrated_factory, account_id, f"v{i}")

        assert len(await recent_videos(migrated_factory)) == 10  # default
        assert len(await recent_videos(migrated_factory, limit=2)) == 2
        assert len(await recent_videos(migrated_factory, limit=0)) == 1  # clamped up
        assert len(await recent_videos(migrated_factory, limit=5000)) == 100  # clamped down

    async def test_username_filter_is_normalized(self, migrated_factory) -> None:
        first, second = await seed_two_accounts(migrated_factory)
        await seed_video(migrated_factory, first, "1")
        await seed_video(migrated_factory, second, "2")

        rows = await recent_videos(migrated_factory, username="@Acct")

        assert [r["tiktok_video_id"] for r in rows] == ["1"]

    async def test_empty_archive_returns_empty_list(self, migrated_factory) -> None:
        assert await recent_videos(migrated_factory) == []

    async def test_rows_are_plain_json_safe_dicts(self, migrated_factory) -> None:
        account_id = await add_account(migrated_factory)
        await seed_video(migrated_factory, account_id, "1", **DOWNLOAD_SPEC)

        rows = await recent_videos(migrated_factory)
        assert rows == json.loads(json.dumps(rows))
        row = rows[0]
        assert row["username"] == "acct"
        assert row["status"] == "downloaded"
        assert row["file_size"] == 123
        assert row["error_category"] is None


class TestExportVideos:
    async def test_json_round_trips_plain_types(self, migrated_factory) -> None:
        account_id = await add_account(migrated_factory)
        await seed_video(migrated_factory, account_id, "1", **DOWNLOAD_SPEC)

        payload = await export_videos(migrated_factory, "json")
        rows = json.loads(payload)

        assert isinstance(rows, list) and len(rows) == 1
        assert rows[0]["tiktok_video_id"] == "1"
        assert rows[0]["file_size"] == 123

    async def test_csv_header_and_comma_quoting(self, migrated_factory) -> None:
        account_id = await add_account(migrated_factory)
        await seed_video(
            migrated_factory,
            account_id,
            "1",
            description="a,b",
        )

        payload = await export_videos(migrated_factory, "csv")
        parsed = list(csv.reader(io.StringIO(payload)))

        assert parsed[0][0] == "id"
        assert "tiktok_video_id" in parsed[0]
        assert "description" in parsed[0]
        assert parsed[1][parsed[0].index("description")] == "a,b"
        assert parsed[1][parsed[0].index("username")] == "acct"

    @pytest.mark.parametrize("danger", ["=", "+", "-", "@", "\t", "\r"])
    async def test_csv_formula_sanitization_prefixes_dangerous_leading_chars(
        self, migrated_factory, danger: str
    ) -> None:
        account_id = await add_account(migrated_factory)
        await seed_video(migrated_factory, account_id, "1", title=f"{danger}calc()")

        payload = await export_videos(migrated_factory, "csv")
        parsed = list(csv.reader(io.StringIO(payload)))
        cell = parsed[1][parsed[0].index("title")]

        assert cell == f"'{danger}calc()"

    async def test_csv_innocent_text_is_not_touched(self, migrated_factory) -> None:
        account_id = await add_account(migrated_factory)
        await seed_video(migrated_factory, account_id, "1", title="plain title")

        payload = await export_videos(migrated_factory, "csv")
        parsed = list(csv.reader(io.StringIO(payload)))

        assert parsed[1][parsed[0].index("title")] == "plain title"

    async def test_username_filter(self, migrated_factory) -> None:
        first, second = await seed_two_accounts(migrated_factory)
        await seed_video(migrated_factory, first, "1")
        await seed_video(migrated_factory, second, "2")

        rows = json.loads(await export_videos(migrated_factory, "json", username="acct"))

        assert [r["tiktok_video_id"] for r in rows] == ["1"]

    async def test_unknown_format_is_configuration_error(self, migrated_factory) -> None:
        with pytest.raises(ConfigurationError):
            await export_videos(migrated_factory, "xml")


# --- CLI (tmp DATA_DIR + migrated DB, per invocation) ---


def _seed_cli_db(tmp_path: Path, specs: list[dict]) -> None:
    """Migrate tmp DATA_DIR and insert account + video rows synchronously."""
    run_migrations(tmp_path)

    async def seed() -> None:
        engine = create_db_engine(sqlite_url_for(tmp_path))
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                account = MonitoredAccount(username="acct", backfill_status="idle")
                session.add(account)
                await session.flush()
                for spec in specs:
                    session.add(Video(account_id=account.id, **spec))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())


def _downloaded_spec(video_id: str, **overrides) -> dict:
    spec = {
        "tiktok_video_id": video_id,
        "status": "downloaded",
        "downloaded_at": "2026-02-01T10:00:00+00:00",
        "file_size": 10,
    }
    spec.update(overrides)
    return spec


def test_cli_videos_last_lists_recent_videos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _seed_cli_db(
        tmp_path,
        [_downloaded_spec("7300000000000000001"), _downloaded_spec("7300000000000000002")],
    )

    result = cli_runner.invoke(cli_app, ["videos", "last"])
    assert result.exit_code == 0, result.output
    assert "7300000000000000001" in result.output
    assert "7300000000000000002" in result.output
    assert "downloaded" in result.output


def test_cli_videos_last_respects_n_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _seed_cli_db(tmp_path, [_downloaded_spec("1"), _downloaded_spec("2")])

    result = cli_runner.invoke(cli_app, ["videos", "last", "1"])
    assert result.exit_code == 0, result.output
    assert result.output.count("video=") == 1


def test_cli_videos_last_empty_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    run_migrations(tmp_path)

    result = cli_runner.invoke(cli_app, ["videos", "last"])
    assert result.exit_code == 0, result.output
    assert "no videos" in result.output


def test_cli_videos_export_json_payload_is_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _seed_cli_db(tmp_path, [_downloaded_spec("7300000000000000001")])

    result = cli_runner.invoke(cli_app, ["videos", "export"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert rows[0]["tiktok_video_id"] == "7300000000000000001"


def test_cli_videos_export_csv_no_markup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _seed_cli_db(tmp_path, [_downloaded_spec("1", title="=SUM(A1)")])

    result = cli_runner.invoke(cli_app, ["videos", "export", "--format", "csv"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0].startswith("id,")  # raw header row first
    assert "'=SUM(A1)" in result.output  # T-DEPLOY-16 sanitization
    assert "[bold]" not in result.output  # T-CLI-3: no Rich markup, no wrap


def test_cli_videos_export_unknown_format_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    run_migrations(tmp_path)

    result = cli_runner.invoke(cli_app, ["videos", "export", "--format", "xml"])
    assert result.exit_code == 1
    assert "ERROR" in result.output


def test_cli_videos_integrity_still_loud_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    result = cli_runner.invoke(cli_app, ["videos", "integrity"])
    assert result.exit_code == 1
    assert "not implemented" in result.output

"""services/videos.handle_download_result: the single post-download truth point.

Trampas covered: T-ENGINE-5, T-ENGINE-18, T-ENGINE-24, T-ASYNC-8, T-ASYNC-14/15,
T-BACKFILL-13/15/21, T-DATA-1, T-DATA-3, T-DATA-10. Reglas: 3.3, 3.1, 4.4, 4.7.

All doubles are deterministic: tmp_path files, injected sync ffprobe/sha fakes,
a migrated real SQLite database (T-DATA-1: the pending INSERT passes the real
CHECK). The production caller (monitor/backfill jobs) is pending by plan order
(M3/M4); these tests pin the service contract.
"""

import asyncio
import errno
import hashlib
import json
import os
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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


class FakeRetry:
    """Async retry_fn double: records invocation order, returns a fixed path."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, video_id: str, retry_index: int) -> Path:
        self.calls.append((video_id, retry_index))
        return self.path


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
    retry = FakeRetry(tmp_path / "elsewhere.mp4")
    events = Recorder()

    result = await handle_download_result(
        migrated_factory,
        row_id,
        downloaded,
        "slide1",
        account_id,
        expected_has_video=False,
        ffprobe_fn=probe_fn(NO_VIDEO_PROBE),
        retry_fn=retry,
        archive=archive,
        on_event=events,
    )

    assert (result.outcome, result.error_category) == ("skipped", None)
    assert await archive.contains("slide1")  # dedupe entry ADDED
    assert retry.calls == []  # NO retry for an expected slideshow
    async with migrated_factory() as session:
        video = await session.get(Video, row_id)
    assert video.status == "skipped"
    assert video.error_category is None
    assert [e["status"] for e in events.events] == ["skipped"]


# --- T-ENGINE-5 cause 2: degraded response (real failure) ---


async def test_degraded_discards_archive_before_retry_then_succeeds(
    tmp_path: Path, migrated_factory
) -> None:
    account_id = await add_account(migrated_factory)
    row_id = await add_pending_video(migrated_factory, account_id)
    first = tmp_path / "videos" / "acct" / "123.retry-1.mp4"
    second = tmp_path / "videos" / "acct" / "123.retry-2.mp4"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"audio only")
    second.write_bytes(VIDEO_BYTES)
    archive = DownloadArchive(tmp_path / "download_archive.txt", migrated_factory)
    await archive.add("123")
    archive_order = FakeArchive()
    retry = FakeRetry(second)
    events = Recorder()

    # Wrap the real archive so remove order vs retry_fn order is observable.
    real_remove = archive.remove

    async def spy_remove(video_id: str) -> bool:
        archive_order.order.append(f"archive.remove:{video_id}")
        return await real_remove(video_id)

    archive.remove = spy_remove  # type: ignore[method-assign]

    async def retry_recorder(video_id: str, retry_index: int) -> Path:
        archive_order.order.append(f"retry:{video_id}")
        return await retry(video_id, retry_index=retry_index)

    result = await handle_download_result(
        migrated_factory,
        row_id,
        first,
        "123",
        account_id,
        ffprobe_fn=probing_twice(NO_VIDEO_PROBE, GOOD_PROBE),
        retry_fn=retry_recorder,
        archive=archive,
        on_event=events,
    )

    assert result.outcome == "downloaded"
    # T-ENGINE-18: the archive entry is discarded BEFORE the fallback retry.
    assert archive_order.order == ["archive.remove:123", "retry:123"]
    final = Path(result.file_path)
    assert final.name == "123.mp4"
    assert final.read_bytes() == VIDEO_BYTES
    async with migrated_factory() as session:
        video = await session.get(Video, row_id)
    assert video.status == "downloaded"
    assert video.file_hash == VIDEO_SHA256
    assert video.retry_count == 1  # base_retry_count 0 + 1 attempt
    assert [e["status"] for e in events.events] == ["downloaded"]  # exactly once


async def test_degraded_retry_fails_again_is_integrity_failed(
    tmp_path: Path, migrated_factory
) -> None:
    account_id = await add_account(migrated_factory)
    row_id = await add_pending_video(migrated_factory, account_id)
    first = tmp_path / "videos" / "acct" / "123.retry-1.mp4"
    second = tmp_path / "videos" / "acct" / "123.retry-2.mp4"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"audio only")
    second.write_bytes(b"still audio only")
    events = Recorder()

    result = await handle_download_result(
        migrated_factory,
        row_id,
        first,
        "123",
        account_id,
        ffprobe_fn=probing_twice(NO_VIDEO_PROBE, NO_VIDEO_PROBE),
        retry_fn=FakeRetry(second),
        on_event=events,
    )

    assert (result.outcome, result.error_category) == ("failed", "integrity")
    async with migrated_factory() as session:
        video = await session.get(Video, row_id)
    assert video.status == "failed"
    assert video.error_category == "integrity"
    assert video.retry_count == 1  # base 0 + 1 attempt
    assert [e["status"] for e in events.events] == ["failed"]


async def test_degraded_without_retry_fn_is_integrity_failed(
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


async def test_degraded_with_exhausted_retry_budget_skips_retry_fn(
    tmp_path: Path, migrated_factory
) -> None:
    """The fallback retry fires only when base_retry_count < 1."""
    account_id = await add_account(migrated_factory)
    row_id = await add_pending_video(migrated_factory, account_id)
    downloaded = tmp_path / "videos" / "acct" / "123.retry-2.mp4"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"audio only")
    retry = FakeRetry(tmp_path / "never.mp4")

    result = await handle_download_result(
        migrated_factory,
        row_id,
        downloaded,
        "123",
        account_id,
        base_retry_count=1,
        ffprobe_fn=probe_fn(NO_VIDEO_PROBE),
        retry_fn=retry,
    )

    assert (result.outcome, result.error_category) == ("failed", "integrity")
    assert retry.calls == []


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

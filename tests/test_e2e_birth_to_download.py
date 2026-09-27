"""E2E acceptance (12.1 M4): birth -> download of a simulated video end-to-end.

The REAL service composition runs against a REAL migrated file SQLite
database on tmp_path (13.1 deterministic tier: no network, no real yt-dlp,
no real clock, bounded waits only). ONLY the download engine and the ffprobe
probe are faked; pacer (cooldown 0/0, T-ENGINE-8), semaphore, archive,
notifications and SHA-256 are the REAL production components, and the
lifecycle follows 3.3 exactly: discover -> INSERT pending ->
download_pendings -> terminal state via handle_download_result (4.7, the
single truth point).

Traps covered: T-ENGINE-8, T-ENGINE-14, T-BACKFILL-21 (paths derived from
DATA_DIR via the real outtmpl), T-DATA-1 (pending INSERT against the real
CHECK). Rules: 12.1 M4, 3.3, 4.5, 4.7, 5.3.
"""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.archive import DownloadArchive
from tikdown_rs.core.config import Settings
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.core.notifications import InMemoryNotificationService
from tikdown_rs.core.pacing import DownloadPacer
from tikdown_rs.core.paths import outtmpl_for, videos_root
from tikdown_rs.models import Cookie, DaemonState, MonitoredAccount, Video
from tikdown_rs.services.accounts import add_account
from tikdown_rs.services.backfill import run_backfill
from tikdown_rs.services.backfill_ops import queue_backfill
from tikdown_rs.services.monitor import (
    discover_new_videos,
    run_monitor_cycle_once,
    set_monitor_running,
)

VIDEO_ID = "777"
VIDEO_BYTES = b"simulated tiktok video payload for the M4 acceptance test"
VIDEO_SHA256 = hashlib.sha256(VIDEO_BYTES).hexdigest()
GOOD_PROBE = {
    "streams": [{"codec_name": "h264", "width": 1280, "height": 720}],
    "format": {"duration": "3.5"},
}
# A slideshow (photo post) probe: NO video stream (4.7, T-ENGINE-5).
NO_VIDEO_PROBE = {"streams": [], "format": {"duration": "0.0"}}


def _entry(video_id: str = VIDEO_ID, duration: float | None = 3.5) -> dict:
    return {
        "id": video_id,
        "url": f"https://www.tiktok.com/@acct/video/{video_id}",
        "title": f"title {video_id}",
        "description": None,
        "duration": duration,
        "upload_date": "20260101",
        "uploader": "acct",
    }


class FakeEngine:
    """Engine double (4.8): real-listing-shaped entries; download writes REAL
    bytes to the REAL DATA_DIR-derived outtmpl path (T-BACKFILL-21).

    ``records_archive=True`` simulates yt-dlp's ``--download-archive``
    behavior on a completed download (the ENGINE writes the archive entry,
    never handle_download_result); disabled for the slideshow case where the
    dedupe add is owned by handle_download_result (T-ENGINE-5)."""

    def __init__(self, entries: list[dict], data_dir: Path, records_archive: bool = True) -> None:
        self.entries = entries
        self.data_dir = data_dir
        self.records_archive = records_archive
        self.list_calls = 0
        self.download_order: list[str] = []

    def list_videos(self, username: str, max_entries: int | None = None) -> list[dict]:
        self.list_calls += 1
        return [dict(e) for e in self.entries]

    async def download(
        self,
        page_url: str,
        video_id: str,
        uploader: str | None,
        retry_index: int = 0,
        archive=None,
    ) -> Path:
        self.download_order.append(video_id)
        # The REAL production outtmpl (4.5), formatted exactly as yt-dlp would.
        path = Path(
            outtmpl_for(self.data_dir) % {"uploader": uploader, "id": video_id, "ext": "mp4"}
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(VIDEO_BYTES)
        if self.records_archive and archive is not None:  # yt-dlp --download-archive
            await archive.add(video_id)
        return path

    def extract_profile(self, username: str) -> dict:
        raise NotImplementedError

    def validate_cookie(self, probe_fn=None) -> str:
        raise NotImplementedError


def ffprobe_ok(_path: Path) -> dict:
    return json.loads(json.dumps(GOOD_PROBE))


def ffprobe_no_video(_path: Path) -> dict:
    """Slideshow response: the downloaded file has NO video stream (T-ENGINE-5)."""
    return json.loads(json.dumps(NO_VIDEO_PROBE))


@pytest.fixture
async def factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Session factory over a REAL migrated file database."""
    await asyncio.to_thread(run_migrations, tmp_path)
    db_engine = create_db_engine(sqlite_url_for(tmp_path))
    session_factory = make_session_factory(db_engine)
    yield session_factory
    await db_engine.dispose()


def _real_stack(tmp_path: Path, factory: async_sessionmaker[AsyncSession]) -> dict:
    """The REAL production components: pacer 0/0 (disabled, T-ENGINE-8),
    semaphore max 1, real DownloadArchive, real InMemoryNotificationService
    bridged over the SYNC dict channel (T-BACKFILL-15)."""
    pacer = DownloadPacer(
        factory,
        Settings(data_dir=tmp_path),
        min_seconds=0,
        max_seconds=0,  # T-ENGINE-8: both 0 -> cooldown disabled
    )
    archive = DownloadArchive(tmp_path / "download_archive.txt", factory)
    notifications = InMemoryNotificationService()

    def on_event(payload: dict) -> None:  # SYNC, never awaited
        notifications.emit(payload["event"], payload)

    return {
        "pacer": pacer,
        "semaphore": asyncio.Semaphore(1),
        "archive": archive,
        "notifications": notifications,
        "on_event": on_event,
    }


async def _setup_monitor_account(factory) -> int:
    """Account via the REAL accounts service + one working cookie + monitor on."""
    account_id = await add_account(factory, "acct", mode="monitor")
    async with factory() as session:
        session.add(Cookie(label="c", cookie_blob=b"netscape", validation_state="valid"))
        await session.commit()
    await set_monitor_running(factory, True)
    return account_id


async def _get_video(factory, account_id: int) -> Video:
    async with factory() as session:
        return (
            (await session.execute(select(Video).where(Video.account_id == account_id)))
            .scalars()
            .one()
        )


async def _get_account(factory, account_id: int) -> MonitoredAccount:
    async with factory() as session:
        return await session.get(MonitoredAccount, account_id)


async def _monitor_running(factory) -> bool:
    async with factory() as session:
        state = await session.get(DaemonState, 1)
    return bool(state is not None and state.monitor_running)


def _event_names(notifications: InMemoryNotificationService) -> list[str]:
    return [emitted.event for emitted in notifications.events]


async def test_birth_to_download_end_to_end(tmp_path, factory) -> None:
    """12.1 M4 acceptance: one monitor-mode video from birth to downloaded.

    Act 1: the REAL discovery path (discover_new_videos, the exact helper the
    heartbeat composition calls) INSERTs status='pending' (3.3 step 1, real
    CHECK); the pending row is the persisted hand-off state and is asserted
    BEFORE any download. Act 2: the REAL heartbeat composition
    (run_monitor_cycle_once, 5.3/T-DATA-2) completes the lifecycle: terminal
    state written ONLY by handle_download_result (3.3 step 3): file at the
    real outtmpl path, correct SHA-256, total_disk_bytes in the same commit
    (4.7 step 4), archive entry (3.6), event trail (6.2), monitor_running
    untouched (5.3).
    """
    account_id = await _setup_monitor_account(factory)
    engine = FakeEngine([_entry()], tmp_path)
    stack = _real_stack(tmp_path, factory)
    notifications = stack["notifications"]

    # --- Act 1: birth (3.3 step 1) through the REAL discovery path ---
    assert (
        await discover_new_videos(factory, "acct", engine=engine, on_event=stack["on_event"]) == 1
    )

    # 3.3 step 1: the video row exists as 'pending' BEFORE any download.
    pending = await _get_video(factory, account_id)
    assert pending.status == "pending"
    assert pending.tiktok_video_id == VIDEO_ID
    assert pending.url == f"https://www.tiktok.com/@acct/video/{VIDEO_ID}"
    assert _event_names(notifications) == ["monitor.video_discovered"]
    assert await _monitor_running(factory) is True

    # --- Act 2: the REAL heartbeat composition reaches the terminal state ---
    cycle2 = await run_monitor_cycle_once(
        factory,
        engine=engine,
        pacer=stack["pacer"],
        semaphore=stack["semaphore"],
        archive=stack["archive"],
        on_event=stack["on_event"],
        ffprobe_fn=ffprobe_ok,
    )
    assert cycle2["downloaded"] == 1
    assert cycle2["discovered"] == 0  # throttled re-listing finds nothing new
    assert engine.list_calls == 1  # the re-listing was throttled (4.6)

    video = await _get_video(factory, account_id)
    expected_path = videos_root(tmp_path) / "acct" / f"{VIDEO_ID}.mp4"
    assert video.status == "downloaded"
    assert Path(video.local_path) == expected_path
    assert video.local_path == str(expected_path)  # absolute, DATA_DIR-derived
    assert Path(video.local_path).is_absolute()
    assert Path(video.local_path).is_relative_to(videos_root(tmp_path))
    assert Path(video.local_path).read_bytes() == VIDEO_BYTES
    assert video.file_size == len(VIDEO_BYTES)
    assert video.file_hash == VIDEO_SHA256  # REAL SHA-256 of the real file
    assert video.downloaded_at is not None
    assert video.error_category is None

    # 4.7 step 4: total_disk_bytes rides the same commit (never a recount).
    account = await _get_account(factory, account_id)
    assert account.total_disk_bytes == len(VIDEO_BYTES)

    # 3.6: the REAL archive contains the video id (file + mirror).
    assert await stack["archive"].contains(VIDEO_ID) is True
    assert "tiktok 777" in (tmp_path / "download_archive.txt").read_text(encoding="ascii")

    # Full event trail, in order: discovered -> downloaded (6.2).
    assert _event_names(notifications) == [
        "monitor.video_discovered",
        "download.downloaded",
    ]
    downloaded_payload = notifications.events[1].payload
    assert downloaded_payload["video_id"] == VIDEO_ID
    assert downloaded_payload["file_path"] == str(expected_path)

    # 5.3: the heartbeat flag was never disturbed by the cycle.
    assert await _monitor_running(factory) is True


async def test_birth_to_download_with_backfill_queue_alternative(tmp_path, factory) -> None:
    """Same simulated video through the OTHER funnel (12.1 M4): mode='history',
    queue_backfill -> run_backfill (M3) -> downloaded. Both funnels converge on
    handle_download_result (4.7, the single truth point, 3.3 step 3)."""
    account_id = await add_account(factory, "acct", mode="history")
    async with factory() as session:
        session.add(Cookie(label="c", cookie_blob=b"netscape", validation_state="valid"))
        await session.commit()
    engine = FakeEngine([_entry()], tmp_path)
    stack = _real_stack(tmp_path, factory)

    assert await queue_backfill(factory, "acct", queue=True) == "queued"
    status = await run_backfill(
        factory,
        account_id,
        engine=engine,
        pacer=stack["pacer"],
        semaphore=stack["semaphore"],
        archive=stack["archive"],
        on_event=stack["on_event"],
        ffprobe_fn=ffprobe_ok,
    )
    assert status == "completed"

    video = await _get_video(factory, account_id)
    assert video.status == "downloaded"
    assert Path(video.local_path) == videos_root(tmp_path) / "acct" / f"{VIDEO_ID}.mp4"
    assert video.file_hash == VIDEO_SHA256
    account = await _get_account(factory, account_id)
    assert account.total_disk_bytes == len(VIDEO_BYTES)
    assert await stack["archive"].contains(VIDEO_ID) is True
    # Both funnels emit the SAME terminal event through the one truth point.
    assert "download.downloaded" in _event_names(notifications=stack["notifications"])
    assert "backfill.completed" in _event_names(notifications=stack["notifications"])


async def test_birth_skipped_slideshow(tmp_path, factory) -> None:
    """Slideshow variant (4.7/T-ENGINE-5 cause 1): a listing entry without
    duration is a photo post -> pending -> status='skipped' + archive entry,
    NO retries, download.skipped event (never the degraded-response path)."""
    account_id = await _setup_monitor_account(factory)
    engine = FakeEngine([_entry(duration=None)], tmp_path, records_archive=False)
    stack = _real_stack(tmp_path, factory)
    notifications = stack["notifications"]

    assert (
        await discover_new_videos(factory, "acct", engine=engine, on_event=stack["on_event"]) == 1
    )
    pending = await _get_video(factory, account_id)
    assert pending.status == "pending"
    assert pending.duration is None

    cycle2 = await run_monitor_cycle_once(
        factory,
        engine=engine,
        pacer=stack["pacer"],
        semaphore=stack["semaphore"],
        archive=stack["archive"],
        on_event=stack["on_event"],
        ffprobe_fn=ffprobe_no_video,  # the downloaded file has no video stream
    )
    assert cycle2["skipped"] == 1
    assert cycle2["failed"] == 0

    video = await _get_video(factory, account_id)
    assert video.status == "skipped"
    assert video.error_category is None
    assert video.retry_count == 0  # NO retries for an expected slideshow
    assert engine.download_order == [VIDEO_ID]  # exactly one download attempt
    assert await stack["archive"].contains(VIDEO_ID) is True  # dedupe add (3.3)
    assert _event_names(notifications) == [
        "monitor.video_discovered",
        "download.skipped",
    ]

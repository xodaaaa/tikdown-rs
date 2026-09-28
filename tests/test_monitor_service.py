"""services/monitor: throttle-accurate discovery + pending lifecycle (3.3, 4.6).

Traps covered: T-BACKFILL-1 (NULL last_check_at is ALWAYS checked),
T-BACKFILL-3 (missing upload_date never NULL), T-ENGINE-4 ('keeps sending
the same page' is transient, never an account failure), T-DATA-1 (pending
insert against the real migrated CHECK), 5.8 (global stop without cookies),
8.1/8.2 (network/disk pauses).

All doubles are deterministic (pattern from tests/test_backfill_service.py):
a migrated real file SQLite database, a fake engine, a no-op pacer, an
in-memory event recorder and injected ffprobe/sha fakes. No network, no
yt-dlp. The throttle reference clock is the real clock: last_check_at is
seeded relative to now, so no sleeps and no frozen clock are needed.
"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import tikdown_rs.services.monitor as monitor_module
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.models import Cookie, DaemonState, MonitoredAccount, Video
from tikdown_rs.services.monitor import (
    THROTTLE_SECONDS,
    discover_new_videos,
    download_pendings,
    run_monitor_cycle_once,
)

VIDEO_BYTES = b"fake tiktok video payload for sha256"
VIDEO_SHA256 = hashlib.sha256(VIDEO_BYTES).hexdigest()
GOOD_PROBE = {
    "streams": [{"codec_name": "h264", "width": 1280, "height": 720}],
    "format": {"duration": "3.5"},
}


def _entry(video_id: str, upload_date: str = "20260101") -> dict:
    return {
        "id": video_id,
        "url": f"https://www.tiktok.com/@acct/video/{video_id}",
        "title": f"title {video_id}",
        "description": None,
        "duration": 3.5,
        "upload_date": upload_date,
        "uploader": "acct",
    }


class FakeEngine:
    """Listing + download double: fixed entries, per-video failures, call log."""

    # M23: per-test root injected by the autouse fixture (pytest tmp_path);
    # no tempfile.mkdtemp leak that outlives the test.
    tmp_root: Path = Path(".")

    def __init__(
        self,
        entries: list[dict] | None = None,
        fail: dict[str, str] | None = None,
        list_error: Exception | None = None,
    ) -> None:
        self.entries = entries or []
        self.fail = fail or {}
        self.list_error = list_error
        self.list_calls = 0
        self.download_order: list[str] = []
        self.download_dir = self.tmp_root / "fake_engine_downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def list_videos(self, username: str, max_entries: int | None = None) -> list[dict]:
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
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
        if video_id in self.fail:
            raise RuntimeError(self.fail[video_id])
        path = self.download_dir / f"{video_id}.mp4"
        path.write_bytes(VIDEO_BYTES)
        return path

    def extract_profile(self, username: str) -> dict:
        raise NotImplementedError

    def validate_cookie(self, probe_fn=None) -> str:
        raise NotImplementedError


class FakePacer:
    """No-op pacer double counting acquires (4.5 usage evidence)."""

    def __init__(self) -> None:
        self.acquires = 0

    async def acquire(self) -> None:
        self.acquires += 1


class _FakeSemaphore:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class InMemoryArchive:
    async def contains(self, video_id: str) -> bool:
        return False

    async def add(self, video_id: str, account_id: int | None = None) -> None:
        pass

    async def remove(self, video_id: str) -> bool:
        return False


class Recorder:
    """Sync event channel recorder (T-BACKFILL-15)."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [e["event"] for e in self.events]


def ffprobe_ok(_path: Path) -> dict:
    return json.loads(json.dumps(GOOD_PROBE))


@pytest.fixture(autouse=True)
def _engine_tmp_root(tmp_path: Path, monkeypatch) -> None:
    """M23: fake-engine download bytes land under the test's own tmp_path."""
    monkeypatch.setattr(FakeEngine, "tmp_root", tmp_path)


def sha_ok(_path: Path) -> str:
    return VIDEO_SHA256


def make_kwargs(engine: FakeEngine, pacer: FakePacer) -> dict:
    return {
        "engine": engine,
        "pacer": pacer,
        "semaphore": _FakeSemaphore(),
        "archive": InMemoryArchive(),
        "ffprobe_fn": ffprobe_ok,
        "sha256_fn": sha_ok,
    }


def _download_kwargs(engine: FakeEngine, pacer: FakePacer | None = None) -> dict:
    """download_pendings kwargs: everything the lifecycle call needs."""
    return {
        "pacer": pacer or FakePacer(),
        "semaphore": _FakeSemaphore(),
        "archive": InMemoryArchive(),
        "ffprobe_fn": ffprobe_ok,
        "sha256_fn": sha_ok,
    }


@pytest.fixture
async def factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Session factory over a REAL migrated file database (T-DATA-1 pattern)."""
    await asyncio.to_thread(run_migrations, tmp_path)
    db_engine = create_db_engine(sqlite_url_for(tmp_path))
    session_factory = make_session_factory(db_engine)
    yield session_factory
    await db_engine.dispose()


async def add_monitor_account(
    factory: async_sessionmaker[AsyncSession],
    username: str = "acct",
    **overrides,
) -> int:
    async with factory() as session:
        account = MonitoredAccount(
            username=username, mode="monitor", backfill_status="completed", **overrides
        )
        session.add(account)
        await session.commit()
        return account.id


async def add_valid_cookie(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        session.add(Cookie(label="c", cookie_blob=b"netscape", validation_state="valid"))
        await session.commit()


async def set_last_check_at(factory, account_id: int, value: str | None) -> None:
    async with factory() as session:
        await session.execute(
            text("UPDATE monitored_accounts SET last_check_at = :v WHERE id = :id"),
            {"v": value, "id": account_id},
        )
        await session.commit()


async def get_account(factory, account_id: int) -> MonitoredAccount:
    async with factory() as session:
        return await session.get(MonitoredAccount, account_id)


async def get_videos(factory, account_id: int) -> list[Video]:
    async with factory() as session:
        rows = (
            await session.execute(
                select(Video).where(Video.account_id == account_id).order_by(Video.id)
            )
        ).scalars()
        return list(rows.all())


async def set_daemon_state(factory, **values) -> None:
    """Native upsert of the daemon_state singleton (migrations insert NO row)."""
    columns = ", ".join(values)
    params = dict(values)
    updates = ", ".join(f"{name} = :{name}" for name in values)
    async with factory() as session:
        await session.execute(
            text(
                f"INSERT INTO daemon_state (id, {columns}) VALUES (1, :{', :'.join(values)}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}"
            ),
            params,
        )
        await session.commit()


async def get_daemon_state(factory) -> DaemonState | None:
    async with factory() as session:
        return await session.get(DaemonState, 1)


# --- Throttle (T-BACKFILL-1, 4.6: 30 s) ---


async def test_throttle_recent_check_skips_listing(factory) -> None:
    account_id = await add_monitor_account(factory)
    recent = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    await set_last_check_at(factory, account_id, recent)
    engine = FakeEngine([_entry("1")])
    assert await discover_new_videos(factory, "acct", engine=engine) == 0
    assert engine.list_calls == 0
    assert await get_videos(factory, account_id) == []


async def test_null_last_check_is_always_checked(factory) -> None:
    await add_monitor_account(factory)
    engine = FakeEngine([_entry("1")])
    assert await discover_new_videos(factory, "acct", engine=engine) == 1
    assert engine.list_calls == 1


async def test_throttle_expired_check_lists_again(factory) -> None:
    account_id = await add_monitor_account(factory)
    stale = (datetime.now(UTC) - timedelta(seconds=THROTTLE_SECONDS + 1)).isoformat()
    await set_last_check_at(factory, account_id, stale)
    engine = FakeEngine([_entry("1")])
    assert await discover_new_videos(factory, "acct", engine=engine) == 1
    assert engine.list_calls == 1


# --- Discovery: inserts, events, last_check_at ---


async def test_discover_inserts_pending_and_emits(factory) -> None:
    account_id = await add_monitor_account(factory)
    # One already-known video: the discovery must count only NEW inserts.
    async with factory() as session:
        session.add(
            Video(
                tiktok_video_id="2",
                account_id=account_id,
                url="https://www.tiktok.com/@acct/video/2",
                status="downloaded",
            )
        )
        await session.commit()
    engine = FakeEngine([_entry("1"), _entry("2"), _entry("3")])
    recorder = Recorder()
    discovered = await discover_new_videos(factory, "acct", engine=engine, on_event=recorder)
    assert discovered == 2
    videos = await get_videos(factory, account_id)
    assert [(v.tiktok_video_id, v.status) for v in videos] == [
        ("2", "downloaded"),
        ("1", "pending"),
        ("3", "pending"),
    ]
    assert recorder.names() == ["monitor.video_discovered", "monitor.video_discovered"]
    account = await get_account(factory, account_id)
    assert account.last_check_at is not None


async def test_discover_missing_date_uses_newest_listing_date(factory) -> None:
    """T-BACKFILL-3: absent upload_date gets the newest known listing date."""
    account_id = await add_monitor_account(factory)
    engine = FakeEngine([_entry("1", "20260201"), _entry("2", "")])
    await discover_new_videos(factory, "acct", engine=engine)
    videos = {v.tiktok_video_id: v for v in await get_videos(factory, account_id)}
    assert videos["1"].upload_date == "20260201"
    assert videos["2"].upload_date == "20260201"


async def test_transient_listing_keeps_last_check(factory) -> None:
    """T-ENGINE-4: 'keeps sending the same page' is transient, never a failure."""
    account_id = await add_monitor_account(factory)
    marked = "2026-01-01T00:00:00+00:00"
    await set_last_check_at(factory, account_id, marked)
    engine = FakeEngine(list_error=RuntimeError("yt-dlp keeps sending the same page for @acct"))
    recorder = Recorder()
    assert await discover_new_videos(factory, "acct", engine=engine, on_event=recorder) == 0
    account = await get_account(factory, account_id)
    assert account.needs_review is False
    assert account.last_check_at == marked  # NOT a completed check: kept for sooner retry
    assert await get_videos(factory, account_id) == []
    assert recorder.names() == []


async def test_auth_marker_sets_needs_review(factory) -> None:
    account_id = await add_monitor_account(factory)
    engine = FakeEngine(list_error=RuntimeError("account is private"))
    assert await discover_new_videos(factory, "acct", engine=engine) == 0
    account = await get_account(factory, account_id)
    assert account.needs_review is True


async def test_info_no_videos_updates_last_check(factory) -> None:
    account_id = await add_monitor_account(factory)
    engine = FakeEngine(list_error=RuntimeError("this account does not have any videos posted"))
    assert await discover_new_videos(factory, "acct", engine=engine) == 0
    account = await get_account(factory, account_id)
    assert account.last_check_at is not None  # informative, but a completed check
    assert account.needs_review is False


async def test_unknown_account_is_configuration_error(factory) -> None:
    from tikdown_rs.core.errors import ConfigurationError

    engine = FakeEngine()
    with pytest.raises(ConfigurationError, match="unknown"):
        await discover_new_videos(factory, "ghost", engine=engine)


# --- Pending lifecycle (3.3): download_pendings ---


async def test_download_pendings_downloads_all_pending(factory) -> None:
    account_id = await add_monitor_account(factory)
    await add_valid_cookie(factory)
    engine = FakeEngine([_entry("1"), _entry("2")])
    await discover_new_videos(factory, "acct", engine=engine)
    recorder = Recorder()
    counts = await download_pendings(
        factory,
        "acct",
        engine=engine,
        on_event=recorder,
        **_download_kwargs(engine),
    )
    assert counts["downloaded"] == 2
    assert counts["failed"] == 0
    assert engine.download_order == ["1", "2"]  # created_at ASC (3.3)
    videos = {v.tiktok_video_id: v for v in await get_videos(factory, account_id)}
    assert videos["1"].status == "downloaded"
    assert videos["1"].file_hash == VIDEO_SHA256
    assert recorder.names() == ["download.downloaded", "download.downloaded"]


async def test_download_pendings_failure_does_not_abort_batch(factory) -> None:
    """3.3/T-DATA-1: one failure is recorded, the batch continues."""
    account_id = await add_monitor_account(factory)
    await add_valid_cookie(factory)
    engine = FakeEngine([_entry("1"), _entry("2")], fail={"2": "429 too many requests"})
    await discover_new_videos(factory, "acct", engine=engine)
    counts = await download_pendings(factory, "acct", engine=engine, **_download_kwargs(engine))
    assert counts["downloaded"] == 1
    assert counts["failed"] == 1
    videos = {v.tiktok_video_id: v for v in await get_videos(factory, account_id)}
    assert videos["1"].status == "downloaded"
    assert videos["2"].status == "failed"


async def test_download_pendings_global_pause_returns_immediately(factory) -> None:
    await add_monitor_account(factory)
    await add_valid_cookie(factory)
    await set_daemon_state(factory, downloads_paused=True)
    engine = FakeEngine([_entry("1")])
    await discover_new_videos(factory, "acct", engine=engine)
    counts = await download_pendings(factory, "acct", engine=engine, **_download_kwargs(engine))
    assert counts["paused"] is True
    assert engine.download_order == []


async def test_download_pendings_network_offline_returns_immediately(factory) -> None:
    await add_monitor_account(factory)
    await add_valid_cookie(factory)
    engine = FakeEngine([_entry("1")])
    offline = asyncio.Event()  # created NOT set (8.1)
    counts = await download_pendings(
        factory,
        "acct",
        engine=engine,
        network_available=offline,
        **_download_kwargs(engine),
    )
    assert counts["paused"] is True
    assert engine.download_order == []


async def test_download_pendings_no_cookies_stops_monitor_globally(factory) -> None:
    """5.8: the monitor stops GLOBALLY when no working cookie is left."""
    await add_monitor_account(factory)
    await set_daemon_state(factory, monitor_running=True)
    engine = FakeEngine([_entry("1")])
    await discover_new_videos(factory, "acct", engine=engine)
    recorder = Recorder()
    counts = await download_pendings(
        factory,
        "acct",
        engine=engine,
        on_event=recorder,
        **_download_kwargs(engine),
    )
    assert counts["stopped"] is True
    assert engine.download_order == []
    state = await get_daemon_state(factory)
    assert state.monitor_running is False
    assert recorder.names() == ["monitor.stopped_no_cookies"]


# --- Cycle orchestration ---


async def test_cycle_processes_only_active_monitor_accounts(factory) -> None:
    await add_valid_cookie(factory)
    await add_monitor_account(factory, "active")
    await add_monitor_account(factory, "paused_acct", paused=True)
    engine = FakeEngine([_entry("1")])
    counts = await run_monitor_cycle_once(factory, **make_kwargs(engine, FakePacer()))
    assert engine.list_calls == 1  # the paused account was never listed
    assert counts["accounts"] == 1
    assert counts["discovered"] == 1


async def test_cycle_isolates_failing_account(factory) -> None:
    """One broken account must not kill the cycle for the others."""
    await add_valid_cookie(factory)
    await add_monitor_account(factory, "broken")
    await add_monitor_account(factory, "healthy")
    engine = FakeEngine([_entry("1")])
    real_discover = monitor_module.discover_new_videos

    async def selective_discover(session_factory, username, **kwargs):
        if username == "broken":
            raise RuntimeError("unexpected database exploded")
        return await real_discover(session_factory, username, **kwargs)

    original = monitor_module.discover_new_videos
    monitor_module.discover_new_videos = selective_discover
    try:
        counts = await run_monitor_cycle_once(factory, **make_kwargs(engine, FakePacer()))
    finally:
        monitor_module.discover_new_videos = original
    assert counts["accounts"] == 1
    assert counts["discovered"] == 1
    assert engine.list_calls == 1


async def test_cycle_without_cookies_stops_before_any_account(factory) -> None:
    await add_monitor_account(factory, "acct")
    engine = FakeEngine([_entry("1")])
    recorder = Recorder()
    counts = await run_monitor_cycle_once(
        factory,
        engine=engine,
        pacer=FakePacer(),
        semaphore=_FakeSemaphore(),
        archive=InMemoryArchive(),
        on_event=recorder,
    )
    assert counts["stopped"] is True
    assert engine.list_calls == 0
    assert recorder.names() == ["monitor.stopped_no_cookies"]


# --- B1 (T-ASYNC-8): blocking listing must run OFF the event loop thread ---


async def test_monitor_listing_runs_off_the_loop_thread(factory) -> None:
    """T-ASYNC-8/§1.1.3 (B1/JD-A-001): discover must offload the blocking
    yt-dlp listing to a worker thread; on-loop, the off-loop assertion raises,
    the account is skipped and discovery counts 0."""
    await add_valid_cookie(factory)
    await add_monitor_account(factory)

    def _assert_off_loop() -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise AssertionError("blocking yt-dlp listing ran on the event loop thread")

    class OffLoopListEngine(FakeEngine):
        def list_videos(self, username: str, max_entries: int | None = None) -> list[dict]:
            _assert_off_loop()
            return self.entries

    engine = OffLoopListEngine([_entry("111")])
    discovered = await discover_new_videos(factory, "acct", engine=engine)
    assert discovered == 1

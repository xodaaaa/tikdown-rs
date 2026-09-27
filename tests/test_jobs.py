"""Daemon job bodies (5.3): heartbeat hot-start, backfill-collect, selfcheck,
cookies-validate, disk-check, network-probe, profile-refresh, contention
window persistence (5.6, T-DB-14).

All doubles are deterministic (pattern from tests/test_monitor_service.py):
an in-memory migrated SQLite database, a fake engine, a 0/0 disabled pacer,
injected ffprobe/sha fakes and an InMemoryNotificationService. No network,
no yt-dlp. Traps covered: T-ASYNC-16, T-BACKFILL-13, T-ENGINE-28, T-DATA-2,
T-DB-14.
"""

import asyncio
import hashlib
import json
import logging
import tempfile
import time
from pathlib import Path

from sqlalchemy import select

import tikdown_rs.daemon.run as daemon_run
from tikdown_rs.core.archive import DownloadArchive
from tikdown_rs.core.config import Settings
from tikdown_rs.core.daemon_state import (
    read_status,
    record_last_known_good_ytdlp,
)
from tikdown_rs.core.db import (
    create_db_engine,
    db_busy_count_5min,
    make_session_factory,
    record_db_locked_error,
    reset_contention_window,
)
from tikdown_rs.core.network_monitor import NetworkMonitor
from tikdown_rs.core.notifications import InMemoryNotificationService
from tikdown_rs.daemon.run import (
    DaemonComponents,
    _backfill_collect_job,
    _cookies_validate_job,
    _disk_check_job,
    _heartbeat_job,
    _network_probe_job,
    _profile_refresh_job,
    _selfcheck_job,
)
from tikdown_rs.models import (
    Base,
    Cookie,
    MonitoredAccount,
    Video,
    acquire_backfill_slot,
)
from tikdown_rs.services.monitor import set_monitor_running

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
    """Listing + download double: fixed entries, call log (deterministic)."""

    def __init__(self, entries: list[dict] | None = None) -> None:
        self.entries = entries or []
        self.list_calls = 0
        self.download_order: list[str] = []
        self.download_dir = Path(tempfile.mkdtemp(prefix="tikdown_fake_jobs_"))
        self.profiles: dict[str, dict] = {}

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
        path = self.download_dir / f"{video_id}.mp4"
        path.write_bytes(VIDEO_BYTES)
        return path

    def extract_profile(self, username: str) -> dict:
        return self.profiles.get(
            username, {"username": username, "followers": 10, "video_count": 5}
        )

    def validate_cookie(self, probe_fn=None) -> str:
        raise NotImplementedError


def ffprobe_ok(_path: Path) -> dict:
    return json.loads(json.dumps(GOOD_PROBE))


def sha_ok(_path: Path) -> str:
    return VIDEO_SHA256


class FakePacer:
    """No-op pacer double counting acquires (4.5 usage evidence)."""

    async def acquire(self) -> None:
        pass


class FakeSemaphore:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def make_components(
    tmp_path: Path,
    *,
    settings: Settings | None = None,
    engine=...,
    notifications: InMemoryNotificationService | None = None,
    **overrides,
) -> tuple[DaemonComponents, object]:
    """Components over an in-memory migrated DB, pacer 0/0 (no cooldown sleeps)."""
    db_engine = create_db_engine("sqlite+aiosqlite:///:memory:")
    async with db_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = make_session_factory(db_engine)
    settings = settings or Settings(data_dir=tmp_path)
    network_available = asyncio.Event()
    network_available.set()  # T-ENGINE-7: created PRE-SET
    network_monitor = NetworkMonitor(settings, network_available)
    notifications = notifications or InMemoryNotificationService()
    components = DaemonComponents(
        settings=settings,
        session_factory=session_factory,
        engine=FakeEngine() if engine is ... else engine,
        pacer=FakePacer(),
        semaphore=FakeSemaphore(),
        archive=DownloadArchive(tmp_path / "download_archive.txt", session_factory),
        network_monitor=network_monitor,
        network_available=network_available,
        notifications=notifications,
        on_event=daemon_run._notification_on_event(notifications),
        **overrides,
    )
    return components, db_engine


async def _add_account(factory, username: str = "acct", **overrides) -> int:
    async with factory() as session:
        account = MonitoredAccount(
            username=username,
            backfill_status=overrides.pop("backfill_status", "completed"),
            **overrides,
        )
        session.add(account)
        await session.commit()
        return account.id


async def _add_valid_cookie(factory) -> None:
    async with factory() as session:
        session.add(Cookie(label="c", cookie_blob=b"netscape", validation_state="valid"))
        await session.commit()


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("predicate not met before timeout")


# --- heartbeat: hot monitor start (T-CLI-6, T-ASYNC-16) ---


async def test_heartbeat_writes_heartbeat_and_no_cycle_when_monitor_stopped(tmp_path) -> None:
    components, db_engine = await make_components(tmp_path)
    try:
        await _heartbeat_job(components)
        async with components.session_factory() as session:
            row = await read_status(session)
        assert row.last_heartbeat_at is not None
        assert components.monitor_cycle_task is None
    finally:
        await db_engine.dispose()


async def test_heartbeat_hot_start_runs_monitor_cycle(tmp_path) -> None:
    """T-CLI-6: monitor_running=1 + a new video -> one tick discovers and downloads."""
    engine = FakeEngine(entries=[_entry("111")])
    components, db_engine = await make_components(
        tmp_path, engine=engine, ffprobe_fn=ffprobe_ok, sha256_fn=sha_ok
    )
    try:
        await _add_account(components.session_factory, mode="monitor")
        await _add_valid_cookie(components.session_factory)
        await set_monitor_running(components.session_factory, True)

        await _heartbeat_job(components)  # T-ASYNC-16: flag read EVERY execution
        task = components.monitor_cycle_task
        assert task is not None
        await asyncio.wait_for(task, timeout=5.0)

        async with components.session_factory() as session:
            video = (await session.execute(select(Video))).scalars().first()
        assert video is not None
        assert video.status == "downloaded"
    finally:
        await db_engine.dispose()


async def test_heartbeat_refuses_cycle_when_degraded_no_engine(tmp_path, caplog) -> None:
    """No working cookie at startup -> engine None -> the cycle is never launched."""
    components, db_engine = await make_components(tmp_path, engine=None)
    try:
        await set_monitor_running(components.session_factory, True)
        with caplog.at_level(logging.WARNING, logger="tikdown_rs.daemon.run"):
            await _heartbeat_job(components)
        assert components.monitor_cycle_task is None
        assert "degraded" in caplog.text.lower()
    finally:
        await db_engine.dispose()


# --- backfill-collect job (9.1, T-BACKFILL-13, T-DATA-2) ---


async def test_backfill_collect_launches_supervised_run_to_completion(tmp_path, caplog) -> None:
    engine = FakeEngine(entries=[])
    components, db_engine = await make_components(tmp_path, engine=engine)
    try:
        await _add_account(components.session_factory, backfill_status="queued")
        await _add_valid_cookie(components.session_factory)
        with caplog.at_level(logging.INFO, logger="tikdown_rs.daemon.run"):
            await _backfill_collect_job(components)

        async def _completed() -> bool:
            return await _account_status(components.session_factory, "acct") == "completed"

        await _wait_until(_completed)
        assert "job.backfill-collect" in caplog.text  # T-DATA-2: consultable trace
    finally:
        await db_engine.dispose()


async def _account_status(factory, username: str) -> str | None:
    async with factory() as session:
        account = (
            (
                await session.execute(
                    select(MonitoredAccount).where(MonitoredAccount.username == username)
                )
            )
            .scalars()
            .first()
        )
        return account.backfill_status if account is not None else None


async def test_backfill_collect_slot_busy_skips_with_log(tmp_path, caplog) -> None:
    components, db_engine = await make_components(tmp_path)
    try:
        await _add_account(components.session_factory, backfill_status="queued")
        await _add_valid_cookie(components.session_factory)
        async with components.session_factory() as session:
            assert await acquire_backfill_slot(session, "other-owner")
        with caplog.at_level(logging.INFO, logger="tikdown_rs.daemon.run"):
            await _backfill_collect_job(components)
        assert "collected=0" in caplog.text  # slot busy -> skipped, WITH trace
        assert await _account_status(components.session_factory, "acct") == "queued"
    finally:
        await db_engine.dispose()


# --- selfcheck job (T-ENGINE-28) ---


async def test_selfcheck_ok_persists_last_known_good_version(tmp_path) -> None:
    notifications = InMemoryNotificationService()
    components, db_engine = await make_components(
        tmp_path,
        notifications=notifications,
        impersonation_fn=lambda: (True, "private-api", 3),
        which_fn=lambda name: f"/fake-bin/{name}",
        ytdlp_version_fn=lambda: "2026.01.01.000000",
    )
    try:
        await _selfcheck_job(components)
        async with components.session_factory() as session:
            row = await read_status(session)
        assert row.last_known_good_ytdlp_version == "2026.01.01.000000"
        assert row.last_selfcheck_ok is True
        assert "selfcheck.ok" in [e.event for e in notifications.events]
    finally:
        await db_engine.dispose()


async def test_selfcheck_failure_with_new_version_logs_update_regression(tmp_path, caplog) -> None:
    notifications = InMemoryNotificationService()
    components, db_engine = await make_components(
        tmp_path,
        notifications=notifications,
        impersonation_fn=lambda: (False, "curl_cffi-missing", 0),
        which_fn=lambda name: f"/fake-bin/{name}",
        ytdlp_version_fn=lambda: "2026.02.01.000000",
    )
    try:
        async with components.session_factory() as session:
            await record_last_known_good_ytdlp(session, "2025.12.01.000000")
        with caplog.at_level(logging.ERROR, logger="tikdown_rs.daemon.run"):
            await _selfcheck_job(components)
        assert "update regression" in caplog.text
        assert "2025.12.01.000000" in caplog.text
        assert "selfcheck.failed" in [e.event for e in notifications.events]
    finally:
        await db_engine.dispose()


# --- cookies-validate job (7, T-DATA-2) ---


async def test_cookies_validate_job_records_verdicts(tmp_path) -> None:
    notifications = InMemoryNotificationService()
    settings = Settings(data_dir=tmp_path, cookie_validation_url=["https://probe.example/1"])
    components, db_engine = await make_components(
        tmp_path,
        settings=settings,
        notifications=notifications,
        cookie_probe_fn=lambda blob, url, max_entries: True,
        validate_sleep_fn=_instant_sleep,
    )
    try:
        async with components.session_factory() as session:
            session.add(Cookie(label="a", cookie_blob=b"n1", validation_state="inconclusive"))
            session.add(Cookie(label="b", cookie_blob=b"n2", validation_state="inconclusive"))
            await session.commit()
        await _cookies_validate_job(components)
        async with components.session_factory() as session:
            states = (await session.execute(select(Cookie.validation_state))).scalars().all()
        assert states == ["valid", "valid"]
        assert [e.event for e in notifications.events].count("cookie.validated") == 2
    finally:
        await db_engine.dispose()


async def _instant_sleep(_seconds: float) -> None:
    return None


# --- disk-check / network-probe / profile-refresh (T-DATA-2 traces) ---


class _Usage:
    def __init__(self, free_percent: float, total: int = 1_000_000) -> None:
        self.total = total
        self.used = total - int(total * free_percent / 100)
        self.free = total - self.used


async def test_disk_check_job_pauses_under_threshold(tmp_path) -> None:
    components, db_engine = await make_components(tmp_path, disk_usage_fn=lambda _path: _Usage(5.0))
    try:
        await _disk_check_job(components)
        async with components.session_factory() as session:
            row = await read_status(session)
        assert row.downloads_paused is True
    finally:
        await db_engine.dispose()


async def test_network_probe_job_toggles_state(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, network_offline_threshold_consecutive_failures=1)
    components, db_engine = await make_components(tmp_path, settings=settings)

    async def probe_offline(_url: str, _timeout: float) -> bool:
        return False

    async def probe_online(_url: str, _timeout: float) -> bool:
        return True

    components.network_monitor._probe_fn = probe_offline
    try:
        await _network_probe_job(components)
        assert not components.network_available.is_set()
        components.network_monitor._probe_fn = probe_online
        await _network_probe_job(components)
        assert components.network_available.is_set()
    finally:
        await db_engine.dispose()


async def test_profile_refresh_job_updates_counters(tmp_path, caplog) -> None:
    engine = FakeEngine()
    engine.profiles["acct"] = {
        "username": "acct",
        "followers": 42,
        "video_count": 7,
    }
    components, db_engine = await make_components(tmp_path, engine=engine)
    try:
        await _add_account(components.session_factory)
        with caplog.at_level(logging.INFO, logger="tikdown_rs.daemon.run"):
            await _profile_refresh_job(components)
        async with components.session_factory() as session:
            row = (await session.execute(select(MonitoredAccount))).scalars().first()
        assert row.follower_count == 42
        assert "job.profile-refresh" in caplog.text
    finally:
        await db_engine.dispose()


# --- 5.6 contention: counter + heartbeat persistence (T-DB-14) ---


async def test_contention_error_injected_into_listener_increments_counter() -> None:
    reset_contention_window()
    assert record_db_locked_error("OperationalError: database is locked") is True
    assert record_db_locked_error("some other failure") is False
    assert db_busy_count_5min() == 1


async def test_heartbeat_persists_contention_window_to_daemon_state(tmp_path, caplog) -> None:
    reset_contention_window()
    record_db_locked_error("database is locked")
    record_db_locked_error("database is locked")
    notifications = InMemoryNotificationService()
    settings = Settings(data_dir=tmp_path, db_busy_timeout_alert_threshold=1)
    components, db_engine = await make_components(
        tmp_path, settings=settings, notifications=notifications
    )
    try:
        with caplog.at_level(logging.ERROR, logger="tikdown_rs.daemon.run"):
            await _heartbeat_job(components)
        async with components.session_factory() as session:
            row = await read_status(session)
        assert row.db_busy_count_5min == 2  # T-DB-14: read from daemon_state
        assert "daemon.db_contention" in caplog.text  # threshold crossed upward
        assert "daemon.db_contention" in [e.event for e in notifications.events]
        # Edge dedupe: the same count next beat logs NOTHING new.
        caplog.clear()
        await _heartbeat_job(components)
        assert "daemon.db_contention" not in caplog.text
    finally:
        await db_engine.dispose()

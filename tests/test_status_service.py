"""Pure tests for services/status.py (M6 T1: §10.1 gather + ASCII formatter).

The `daemon status` logic moved verbatim out of cli/daemon.py, and the bot's
local copies (dispatcher ``format_status_message`` / ``recent_error_lines`` /
``cookie_counts`` / ``heartbeat_age_seconds``) into one service so the CLI,
the bot and the §10.3 Column 1 dashboard share the same data. In-memory
SQLite + seeded rows, same pattern as tests/test_daemon_state.py.
"""

from datetime import UTC, datetime

import pytest

from tikdown_rs.core.daemon_state import write_heartbeat
from tikdown_rs.core.db import create_db_engine, make_session_factory
from tikdown_rs.models import Base, Cookie, DaemonState, MonitoredAccount, Video
from tikdown_rs.services.status import (
    DaemonStatus,
    format_status_lines,
    gather_status,
    heartbeat_age_seconds,
)

IN_MEMORY_URL = "sqlite+aiosqlite:///:memory:"

NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory():
    """In-memory engine via core/db.py with the schema (singleton row NOT ensured)."""
    engine = create_db_engine(IN_MEMORY_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


async def seed_state(session_factory, **overrides) -> None:
    """Seed the daemon_state singleton row with a fixed heartbeat and overrides."""
    async with session_factory() as session:
        await write_heartbeat(session, pid=4242, db_busy_count=7)
        row = await session.get(DaemonState, 1)
        row.last_heartbeat_at = "2025-01-01T11:59:50+00:00"
        for key, value in overrides.items():
            setattr(row, key, value)
        await session.commit()


async def seed_failed_videos(session_factory, videos: list[dict]) -> None:
    async with session_factory() as session:
        account = MonitoredAccount(username="acct", backfill_status="idle")
        session.add(account)
        await session.flush()
        for spec in videos:
            session.add(Video(account_id=account.id, **spec))
        await session.commit()


class TestHeartbeatAgeSeconds:
    """Moved verbatim from the bot's local copy (was TestHeartbeatAge there)."""

    def test_none_when_missing(self):
        assert heartbeat_age_seconds(None, NOW) is None

    def test_age_seconds(self):
        assert heartbeat_age_seconds("2025-01-01T11:59:30+00:00", NOW) == 30.0

    def test_naive_timestamp_treated_as_utc(self):
        assert heartbeat_age_seconds("2025-01-01T11:59:00", NOW) == 60.0


class TestGatherStatus:
    async def test_returns_none_without_daemon_state_row(self, session_factory):
        async with session_factory() as session:
            assert await gather_status(session) is None

    async def test_maps_row_fields(self, session_factory):
        await seed_state(
            session_factory,
            monitor_running=True,
            last_selfcheck_at="2025-01-01T10:00:00+00:00",
            last_selfcheck_ok=False,
            degraded_reason="impersonation: curl_cffi-missing",
        )
        async with session_factory() as session:
            status = await gather_status(session, now=NOW)
        assert status is not None
        assert status.heartbeat_age_seconds == 10.0
        assert status.monitor_running is True
        assert status.daemon_pid == 4242
        assert status.db_busy_count_5min == 7
        # T-ASYNC-14: in-process counters are None ('n/a') for out-of-process callers.
        assert status.supervised_tasks is None
        assert status.ytdlp_zombie_threads is None
        assert status.last_selfcheck_at == "2025-01-01T10:00:00+00:00"
        assert status.last_selfcheck_ok is False
        assert status.degraded_reason == "impersonation: curl_cffi-missing"
        assert status.downloads_paused is False

    async def test_counts_cookies_by_state(self, session_factory):
        await seed_state(session_factory)
        async with session_factory() as session:
            session.add(Cookie(cookie_blob=b"c", validation_state="valid"))
            session.add(Cookie(cookie_blob=b"c", validation_state="valid"))
            session.add(Cookie(cookie_blob=b"c", validation_state="invalid"))
            await session.commit()
        async with session_factory() as session:
            status = await gather_status(session, now=NOW)
        assert status is not None
        assert status.cookie_counts == {"valid": 2, "invalid": 1}

    async def test_recent_errors_derived_from_videos_table(self, session_factory):
        await seed_state(session_factory)
        await seed_failed_videos(
            session_factory,
            [
                {
                    "tiktok_video_id": "v1",
                    "status": "failed",
                    "error_category": "transient",
                    "error_message": "boom one",
                    "updated_at": "2025-01-01T00:00:00+00:00",
                },
                {
                    "tiktok_video_id": "v2",
                    "status": "failed",
                    "error_category": "definitive",
                    "error_message": "login required",
                    "updated_at": "2025-01-02T00:00:00+00:00",
                },
            ],
        )
        async with session_factory() as session:
            status = await gather_status(session, now=NOW)
        assert status is not None
        assert status.recent_errors == [
            "recent_errors: 2",
            "recent_error: v2 [definitive] login required",
            "recent_error: v1 [transient] boom one",
        ]

    async def test_recent_errors_none_without_failures(self, session_factory):
        await seed_state(session_factory)
        async with session_factory() as session:
            status = await gather_status(session, now=NOW)
        assert status is not None
        assert status.recent_errors == ["recent_errors: none"]

    async def test_recent_errors_capped_at_five(self, session_factory):
        await seed_state(session_factory)
        await seed_failed_videos(
            session_factory,
            [
                {
                    "tiktok_video_id": f"v{i}",
                    "status": "failed",
                    "error_category": "transient",
                    "error_message": "boom",
                    "updated_at": f"2025-01-0{i + 1}T00:00:00+00:00",
                }
                for i in range(7)
            ],
        )
        async with session_factory() as session:
            status = await gather_status(session, now=NOW)
        assert status is not None
        assert status.recent_errors[0] == "recent_errors: 5"
        assert len(status.recent_errors) == 6  # header + 5 errors

    async def test_recent_errors_truncate_long_messages(self, session_factory):
        await seed_state(session_factory)
        long_message = "x" * 300
        await seed_failed_videos(
            session_factory,
            [
                {
                    "tiktok_video_id": "v1",
                    "status": "failed",
                    "error_category": "transient",
                    "error_message": long_message,
                    "updated_at": "2025-01-01T00:00:00+00:00",
                }
            ],
        )
        async with session_factory() as session:
            status = await gather_status(session, now=NOW)
        assert status is not None
        assert status.recent_errors[1] == f"recent_error: v1 [transient] {'x' * 120}"

    async def test_disk_free_percent_when_data_dir_given(self, session_factory, tmp_path):
        await seed_state(session_factory)
        async with session_factory() as session:
            status = await gather_status(session, now=NOW, data_dir=str(tmp_path))
        assert status is not None
        assert isinstance(status.disk_free_percent, float)
        assert 0.0 <= status.disk_free_percent <= 100.0

    async def test_disk_free_percent_none_without_data_dir(self, session_factory):
        await seed_state(session_factory)
        async with session_factory() as session:
            status = await gather_status(session, now=NOW)
        assert status is not None
        assert status.disk_free_percent is None

    async def test_downloads_paused_mapped(self, session_factory):
        await seed_state(session_factory, downloads_paused=True)
        async with session_factory() as session:
            status = await gather_status(session, now=NOW)
        assert status is not None
        assert status.downloads_paused is True


class TestFormatStatusLines:
    def test_full_ascii_output(self):
        status = DaemonStatus(
            heartbeat_age_seconds=42.7,
            monitor_running=True,
            daemon_pid=4321,
            db_busy_count_5min=7,
            supervised_tasks=None,
            ytdlp_zombie_threads=None,
            cookie_counts={"valid": 2, "invalid": 1, "inconclusive": 3},
            last_selfcheck_at="2025-01-01T10:00:00+00:00",
            last_selfcheck_ok=False,
            degraded_reason="impersonation: x",
            recent_errors=["recent_errors: 1", "recent_error: v1 [transient] boom"],
        )
        assert format_status_lines(status) == [
            "heartbeat_age_seconds: 43",
            "monitor_running: 1",
            "daemon_pid: 4321",
            "db_busy_count_5min: 7",
            "supervised_tasks: n/a (in-process)",
            "ytdlp_zombie_threads: n/a (in-process)",
            "cookies_valid: 2",
            "cookies_invalid: 1",
            "cookies_inconclusive: 3",
            "last_selfcheck_at: 2025-01-01T10:00:00+00:00",
            "last_selfcheck_ok: 0",
            "degraded_reason: impersonation: x",
            "recent_errors: 1",
            "recent_error: v1 [transient] boom",
        ]

    def test_unknown_heartbeat(self):
        status = DaemonStatus(
            heartbeat_age_seconds=None,
            monitor_running=False,
            daemon_pid=None,
            db_busy_count_5min=0,
            supervised_tasks=None,
            ytdlp_zombie_threads=None,
            cookie_counts={},
            last_selfcheck_at=None,
            last_selfcheck_ok=None,
            degraded_reason=None,
        )
        lines = format_status_lines(status)
        assert lines[0] == "heartbeat_age_seconds: unknown"
        assert "daemon_pid: none" in lines
        assert "last_selfcheck_at: none" in lines
        assert "last_selfcheck_ok: unknown" in lines
        assert "degraded_reason: none" in lines
        assert "recent_errors: none" in lines

    def test_real_in_process_counters_when_held(self):
        """T-ASYNC-14 seam: a caller holding the live objects prints real counts."""
        status = DaemonStatus(
            heartbeat_age_seconds=5.0,
            monitor_running=False,
            daemon_pid=1,
            db_busy_count_5min=0,
            supervised_tasks=3,
            ytdlp_zombie_threads=2,
            cookie_counts={},
            last_selfcheck_at=None,
            last_selfcheck_ok=None,
            degraded_reason=None,
        )
        lines = format_status_lines(status)
        assert "supervised_tasks: 3" in lines
        assert "ytdlp_zombie_threads: 2" in lines

    def test_output_is_pure_ascii(self):
        status = DaemonStatus(
            heartbeat_age_seconds=None,
            monitor_running=True,
            daemon_pid=None,
            db_busy_count_5min=0,
            supervised_tasks=None,
            ytdlp_zombie_threads=None,
            cookie_counts={"valid": 1},
            last_selfcheck_at=None,
            last_selfcheck_ok=True,
            degraded_reason=None,
            recent_errors=["recent_errors: none"],
        )
        "\n".join(format_status_lines(status)).encode("ascii")


class TestToDict:
    def make_status(self) -> DaemonStatus:
        return DaemonStatus(
            heartbeat_age_seconds=30.0,
            monitor_running=True,
            daemon_pid=4321,
            db_busy_count_5min=7,
            supervised_tasks=None,
            ytdlp_zombie_threads=0,
            cookie_counts={"valid": 2, "invalid": 1},
            last_selfcheck_at="2025-01-01T10:00:00+00:00",
            last_selfcheck_ok=True,
            degraded_reason=None,
            recent_errors=["recent_errors: none"],
            disk_free_percent=88.4,
            downloads_paused=True,
        )

    def test_json_serializable(self):
        import json

        assert json.loads(json.dumps(self.make_status().to_dict())) == self.make_status().to_dict()

    def test_plain_types_only(self):
        plain = (str, int, float, bool, type(None), dict, list)
        for value in self.make_status().to_dict().values():
            assert isinstance(value, plain)

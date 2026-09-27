"""services/backfill: cursor-accurate, cancel-safe account history run (9).

Trampas covered: T-BACKFILL-2/3/5/6/7/8/10/11/13/14/18, T-ENGINE-18.
Reglas: 9.1-9.6, 4.5, 4.6.

All doubles are deterministic (pattern from tests/test_videos_service.py):
a migrated real file SQLite database, a fake engine with controllable entries
and failures, a no-op pacer, an in-memory event recorder and injected
ffprobe/sha fakes. No network, no yt-dlp, no real cookie probing.
"""

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import tikdown_rs.services.backfill as backfill_module
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.models import Cookie, MonitoredAccount
from tikdown_rs.services.backfill import (
    collect_queued_backfills,
    reconcile_stale_backfills,
    run_backfill,
    transition_to_monitor_after_backfill,
)

VIDEO_BYTES = b"fake tiktok video payload for sha256"
VIDEO_SHA256 = hashlib.sha256(VIDEO_BYTES).hexdigest()
GOOD_PROBE = {
    "streams": [{"codec_name": "h264", "width": 1280, "height": 720}],
    "format": {"duration": "3.5"},
}


def _entry(video_id: str, upload_date: str) -> dict:
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
    """Listing + download double: fixed entries, per-video failures, call log.

    ``download`` writes REAL bytes to a temp dir so ``handle_download_result``
    step 1 (file exists, size > 0) passes and the injected ffprobe/sha fakes
    decide the outcome (4.7).
    """

    def __init__(self, entries: list[dict], fail: dict[str, str] | None = None) -> None:
        self.entries = entries
        self.fail = fail or {}
        self.list_calls = 0
        self.download_order: list[str] = []
        self.download_dir = Path(tempfile.mkdtemp(prefix="tikdown_fake_engine_"))

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
    """Archive double: nothing is 'already downloaded' (T-ENGINE-18 surface)."""

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


def ffprobe_ok(_path: Path) -> dict:
    return json.loads(json.dumps(GOOD_PROBE))


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


@pytest.fixture(autouse=True)
def _reset_breakers():
    """The 9.6 breaker is process-wide BY SPEC; tests start it clean."""
    backfill_module._BREAKERS.clear()
    yield
    backfill_module._BREAKERS.clear()


@pytest.fixture
async def factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Session factory over a REAL migrated file database (T-DATA-1 pattern)."""
    await asyncio.to_thread(run_migrations, tmp_path)
    engine = create_db_engine(sqlite_url_for(tmp_path))
    session_factory = make_session_factory(engine)
    yield session_factory
    await engine.dispose()


async def add_account(
    factory: async_sessionmaker[AsyncSession],
    username: str = "acct",
    backfill_status: str = "queued",
    **overrides,
) -> int:
    async with factory() as session:
        account = MonitoredAccount(username=username, backfill_status=backfill_status, **overrides)
        session.add(account)
        await session.commit()
        return account.id


async def add_valid_cookie(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        session.add(Cookie(label="c", cookie_blob=b"netscape", validation_state="valid"))
        await session.commit()


async def get_account(factory, account_id: int) -> MonitoredAccount:
    async with factory() as session:
        return await session.get(MonitoredAccount, account_id)


async def set_downloads_paused(factory, value: bool) -> None:
    """Native upsert of the daemon_state singleton (migrations insert NO row)."""
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO daemon_state (id, downloads_paused) VALUES (1, :v)"
                " ON CONFLICT(id) DO UPDATE SET downloads_paused = :v"
            ),
            {"v": int(value)},
        )
        await session.commit()


async def set_backfill_status(factory, account_id: int, status: str) -> None:
    """External 'another session' status writer (backfill cancel simulation)."""
    async with factory() as session:
        await session.execute(
            text("UPDATE monitored_accounts SET backfill_status = :s WHERE id = :id"),
            {"s": status, "id": account_id},
        )
        await session.commit()


async def slot_owner(factory) -> str | None:
    async with factory() as session:
        return (
            await session.execute(text("SELECT owner FROM backfill_slot WHERE id = 1"))
        ).scalar()


# --- Gates 1/2/3: cookies, queued state, slot (9.1) ---


async def test_no_working_cookie_is_configuration_error(factory) -> None:
    account_id = await add_account(factory)
    engine = FakeEngine([_entry("1", "20260101")])
    with pytest.raises(ConfigurationError, match="no_cookies"):
        await run_backfill(factory, account_id, **make_kwargs(engine, FakePacer()))
    assert engine.list_calls == 0


async def test_not_queued_state_is_configuration_error(factory) -> None:
    await add_valid_cookie(factory)
    account_id = await add_account(factory, backfill_status="completed")
    engine = FakeEngine([_entry("1", "20260101")])
    with pytest.raises(ConfigurationError, match="completed"):
        await run_backfill(factory, account_id, **make_kwargs(engine, FakePacer()))


async def test_slot_busy_is_configuration_error(factory) -> None:
    from tikdown_rs.models import acquire_backfill_slot

    await add_valid_cookie(factory)
    account_id = await add_account(factory)
    engine = FakeEngine([_entry("1", "20260101")])
    async with factory() as session:  # occupy the slot with a foreign owner
        await acquire_backfill_slot(session, "someone-else")
    with pytest.raises(ConfigurationError, match="slot_busy"):
        await run_backfill(factory, account_id, **make_kwargs(engine, FakePacer()))
    assert await slot_owner(factory) == "someone-else"  # never stolen


# --- Happy path: totals, done, cursor, events, slot released ---


async def test_happy_path_persists_totals_done_cursor_and_releases_slot(
    factory, monkeypatch
) -> None:
    await add_valid_cookie(factory)
    account_id = await add_account(factory)
    entries = [_entry("3", "20260103"), _entry("2", "20260102"), _entry("1", "20260101")]
    engine = FakeEngine(entries)
    pacer = FakePacer()
    events = Recorder()

    # T-BACKFILL-5 spy: the total UPDATE must happen AFTER the listing.
    order: list[str] = []
    real_persist_total = backfill_module._persist_total

    async def spy_persist_total(*args, **kwargs):
        order.append("total_update")
        return await real_persist_total(*args, **kwargs)

    monkeypatch.setattr(backfill_module, "_persist_total", spy_persist_total)

    status = await run_backfill(factory, account_id, on_event=events, **make_kwargs(engine, pacer))

    assert status == "completed"
    assert engine.download_order == ["3", "2", "1"]  # listed order (newest first)
    assert pacer.acquires == 3
    # backfill.started/completed events carry no "status" key (notifications
    # catalog, M4 WU1); only the per-video download events do.
    assert [e["status"] for e in events.events if "status" in e] == ["downloaded"] * 3
    assert next(e["event"] for e in events.events) == "backfill.started"
    assert [e["event"] for e in events.events][-1] == "backfill.completed"

    account = await get_account(factory, account_id)
    assert order[0] == "total_update"  # spy fired
    assert account.backfill_total == 3  # T-BACKFILL-5: real count, never 0
    assert account.backfill_done == 3
    assert account.backfill_status == "completed"
    assert account.backfill_cursor == "20260101"  # oldest processed upload_date
    assert engine.list_calls == 1

    async with factory() as session:
        statuses = (
            (await session.execute(text("SELECT status FROM videos ORDER BY tiktok_video_id")))
            .scalars()
            .all()
        )
    assert statuses == ["downloaded", "downloaded", "downloaded"]
    assert await slot_owner(factory) is None  # slot released


# --- T-BACKFILL-2: snapshot cursor, strictly '<' ---


async def test_cursor_snapshot_skips_equal_and_older(factory) -> None:
    await add_valid_cookie(factory)
    account_id = await add_account(factory, backfill_cursor="20260103")
    entries = [
        _entry("5", "20260105"),
        _entry("4", "20260104"),
        _entry("3", "20260103"),  # equal to the cursor: skipped (strict '<')
        _entry("2", "20260102"),
    ]
    engine = FakeEngine(entries)

    status = await run_backfill(factory, account_id, **make_kwargs(engine, FakePacer()))

    assert status == "completed"
    assert engine.download_order == ["5", "4"]  # "3" and "2" never downloaded
    account = await get_account(factory, account_id)
    assert account.backfill_done == 2
    assert account.backfill_cursor == "20260104"  # last processed upload_date


# --- T-BACKFILL-3: absent upload_date keeps the previous cursor ---


async def test_absent_upload_date_keeps_previous_cursor(factory) -> None:
    await add_valid_cookie(factory)
    account_id = await add_account(factory)
    entries = [_entry("A", "20260102"), _entry("B", "")]  # newest first
    engine = FakeEngine(entries)

    status = await run_backfill(factory, account_id, **make_kwargs(engine, FakePacer()))

    assert status == "completed"
    account = await get_account(factory, account_id)
    assert account.backfill_cursor == "20260102"  # B never NULLs it, never stale
    assert account.backfill_done == 2  # B counted as terminal


# --- T-BACKFILL-7/8/10: cancel wins over the progress UPDATE ---


async def test_concurrent_cancel_is_not_overwritten(factory) -> None:
    await add_valid_cookie(factory)
    account_id = await add_account(factory, mode="history", monitor_after_backfill=True)
    engine = FakeEngine([_entry("1", "20260101")])
    events = Recorder()

    # The 'cancel' lands from another session while the download is in flight:
    # the conditional progress UPDATE must lose (rowcount 0 -> early return).
    real_download = engine.download

    async def download_then_cancel(*args, **kwargs):
        await set_backfill_status(factory, account_id, "cancelled")
        return await real_download(*args, **kwargs)

    engine.download = download_then_cancel  # type: ignore[method-assign]

    status = await run_backfill(
        factory, account_id, on_event=events, **make_kwargs(engine, FakePacer())
    )

    assert status == "cancelled"
    account = await get_account(factory, account_id)
    assert account.backfill_status == "cancelled"  # never overwritten to completed
    assert account.mode == "history"  # T-BACKFILL-8: NO --then-monitor transition
    assert account.monitor_after_backfill == 1  # flag NOT consumed
    assert await slot_owner(factory) is None


# --- T-BACKFILL-11: one failed video never aborts the feed ---


async def test_transient_and_definitive_failures_do_not_abort_batch(factory) -> None:
    await add_valid_cookie(factory)
    account_id = await add_account(factory)
    entries = [
        _entry("t403", "20260103"),
        _entry("auth", "20260102"),
        _entry("ok", "20260101"),
    ]
    engine = FakeEngine(
        entries,
        fail={
            "t403": "HTTP Error 403: Forbidden",  # transient
            "auth": "this video is requiring login",  # definitive auth
        },
    )

    status = await run_backfill(factory, account_id, **make_kwargs(engine, FakePacer()))

    assert status == "completed"  # the batch continued to the end
    account = await get_account(factory, account_id)
    assert account.backfill_done == 3
    assert account.backfill_status == "completed"
    async with factory() as session:
        rows = (
            await session.execute(
                text("SELECT tiktok_video_id, status, error_category FROM videos ORDER BY id")
            )
        ).all()
    by_id = {r[0]: r for r in rows}
    assert by_id["t403"][1:] == ("failed", "transient")
    assert by_id["auth"][1:] == ("failed", "definitive")
    assert by_id["ok"][1] == "downloaded"


# --- T-BACKFILL-6: CancelledError mid-listing unwedges ---


async def test_cancelled_mid_listing_marks_paused_with_disk_reason(factory) -> None:
    await add_valid_cookie(factory)
    account_id = await add_account(factory)
    await set_downloads_paused(factory, True)

    class CancelListEngine(FakeEngine):
        def list_videos(self, username, max_entries=None):
            raise asyncio.CancelledError()

    engine = CancelListEngine([_entry("1", "20260101")])
    status = await run_backfill(factory, account_id, **make_kwargs(engine, FakePacer()))

    assert status == "paused"
    account = await get_account(factory, account_id)
    assert account.backfill_status == "paused"
    assert account.backfill_pause_reason == "disk"
    assert await slot_owner(factory) is None  # never leaves the slot held


async def test_cancelled_mid_listing_without_cause_requeues(factory) -> None:
    await add_valid_cookie(factory)
    account_id = await add_account(factory)
    await set_downloads_paused(factory, False)

    class CancelListEngine(FakeEngine):
        def list_videos(self, username, max_entries=None):
            raise asyncio.CancelledError()

    engine = CancelListEngine([_entry("1", "20260101")])
    status = await run_backfill(factory, account_id, **make_kwargs(engine, FakePacer()))

    assert status == "queued"
    account = await get_account(factory, account_id)
    assert account.backfill_status == "queued"
    assert account.backfill_pause_reason is None
    assert await slot_owner(factory) is None


# --- 9.6 circuit breaker ---


async def test_breaker_five_consecutive_auth_failures_pauses(factory) -> None:
    await add_valid_cookie(factory)
    account_id = await add_account(factory)
    entries = [_entry(f"v{i}", f"2026010{i}") for i in range(1, 6)]
    engine = FakeEngine(entries, fail={f"v{i}": "requiring login" for i in range(1, 6)})

    status = await run_backfill(factory, account_id, **make_kwargs(engine, FakePacer()))

    assert status == "paused"
    account = await get_account(factory, account_id)
    assert account.backfill_status == "paused"
    assert account.paused is True
    assert account.needs_review is True
    assert engine.download_order == ["v1", "v2", "v3", "v4", "v5"]  # stopped at 5
    assert await slot_owner(factory) is None


async def test_breaker_transient_resets_consecutive_count(factory) -> None:
    await add_valid_cookie(factory)
    account_id = await add_account(factory)
    entries = [
        _entry("a1", "20260106"),
        _entry("a2", "20260105"),
        _entry("a3", "20260104"),
        _entry("a4", "20260103"),
        _entry("t", "20260102"),  # transient resets the count
        _entry("a5", "20260101"),
    ]
    engine = FakeEngine(
        entries,
        fail={
            "a1": "requiring login",
            "a2": "requiring login",
            "a3": "requiring login",
            "a4": "requiring login",
            "t": "HTTP Error 403: Forbidden",
        },
    )

    status = await run_backfill(factory, account_id, **make_kwargs(engine, FakePacer()))

    assert status == "completed"  # a5 processed: the count was reset by 't'
    account = await get_account(factory, account_id)
    assert account.backfill_status == "completed"
    assert account.needs_review is False


# --- 9.5 same-transaction transition ---


async def test_transition_fires_on_completed_and_is_idempotent(factory) -> None:
    await add_valid_cookie(factory)
    account_id = await add_account(factory, mode="history", monitor_after_backfill=True)
    engine = FakeEngine([_entry("1", "20260101")])

    status = await run_backfill(factory, account_id, **make_kwargs(engine, FakePacer()))

    assert status == "completed"
    async with factory() as session:
        account = await session.get(MonitoredAccount, account_id)
        state = (
            await session.execute(text("SELECT monitor_running FROM daemon_state WHERE id = 1"))
        ).scalar()
    assert account.mode == "monitor"
    assert account.monitor_after_backfill == 0  # flag consumed
    assert state == 1  # T-BACKFILL-18: monitor actually running

    # Idempotent: a second call must NOT fire again.
    assert await transition_to_monitor_after_backfill(factory, account_id) is False


async def test_transition_never_fires_from_failed_or_cancelled(factory) -> None:
    await add_valid_cookie(factory)
    account_id = await add_account(factory, mode="history", monitor_after_backfill=True)
    for status in ("failed", "cancelled"):
        await set_backfill_status(factory, account_id, status)
        assert await transition_to_monitor_after_backfill(factory, account_id) is False
    account = await get_account(factory, account_id)
    assert account.mode == "history"
    assert account.monitor_after_backfill == 1


# --- reconcile: orphan 'backfilling' -> 'queued', paused untouched ---


async def test_reconcile_requeues_orphans_and_skips_paused(factory) -> None:
    orphan = await add_account(factory, username="orphan", backfill_status="backfilling")
    paused = await add_account(factory, username="paused", backfill_status="paused")

    count = await reconcile_stale_backfills(factory)

    assert count == 1  # only the orphan
    assert (await get_account(factory, orphan)).backfill_status == "queued"
    assert (await get_account(factory, paused)).backfill_status == "paused"

    assert await reconcile_stale_backfills(factory) == 0  # idempotent


# --- collect: report-only queue for M4 ---


async def test_collect_returns_would_run_usernames(factory) -> None:
    await add_account(factory, username="queuedacct")
    await add_account(
        factory, username="diskresumed", backfill_status="paused", backfill_pause_reason="disk"
    )
    await add_account(
        factory, username="netresumed", backfill_status="paused", backfill_pause_reason="network"
    )
    await add_account(
        factory, username="diskpaused2", backfill_status="paused", backfill_pause_reason="disk"
    )
    await set_downloads_paused(factory, False)  # disk cause resolved globally

    result = await collect_queued_backfills(factory, engine_factory_fn=lambda account: None)

    # queued + every paused whose cause resolved (both disk rows, and network
    # via the default probe). A queued account is collectible regardless of
    # daemon_state.downloads_paused.
    assert set(result) == {"queuedacct", "diskresumed", "netresumed", "diskpaused2"}


async def test_collect_excludes_disk_paused_while_pause_active(factory) -> None:
    await add_account(factory, username="queuedacct")
    await add_account(
        factory, username="diskpaused", backfill_status="paused", backfill_pause_reason="disk"
    )
    await set_downloads_paused(factory, True)  # disk cause NOT resolved

    result = await collect_queued_backfills(factory, engine_factory_fn=lambda account: None)

    assert set(result) == {"queuedacct"}  # queued stands; disk pause blocks only that row


async def test_collect_excludes_when_slot_busy(factory) -> None:
    from tikdown_rs.models import acquire_backfill_slot

    await add_account(factory, username="queuedacct")
    async with factory() as session:
        await acquire_backfill_slot(session, "backfill:999")

    result = await collect_queued_backfills(factory, engine_factory_fn=lambda account: None)

    assert result == []


async def test_collect_excludes_network_paused_when_offline(factory) -> None:
    await add_account(
        factory, username="netpaused", backfill_status="paused", backfill_pause_reason="network"
    )

    result = await collect_queued_backfills(
        factory, engine_factory_fn=lambda account: None, network_online_fn=lambda: False
    )

    assert result == []


# --- 9.1 slot uniqueness across two concurrent runs ---


async def test_two_concurrent_runs_only_one_wins(factory) -> None:

    await add_valid_cookie(factory)
    account_id = await add_account(factory)
    gate = asyncio.Event()
    started = asyncio.Event()

    class GatedEngine(FakeEngine):
        async def download(self, *args, **kwargs):
            started.set()  # task1 is mid-download with the slot held
            await gate.wait()
            return await super().download(*args, **kwargs)

    engine = GatedEngine([_entry("1", "20260101")])
    task1 = asyncio.create_task(
        run_backfill(factory, account_id, **make_kwargs(engine, FakePacer()))
    )
    # Bounded: resolves ONLY when task1 holds the slot mid-download.
    await asyncio.wait_for(started.wait(), timeout=10.0)

    task2 = asyncio.create_task(
        run_backfill(factory, account_id, **make_kwargs(engine, FakePacer()))
    )
    with pytest.raises(ConfigurationError, match="slot_busy"):
        await asyncio.wait_for(task2, timeout=10.0)

    gate.set()
    assert await asyncio.wait_for(task1, timeout=10.0) == "completed"
    assert await slot_owner(factory) is None

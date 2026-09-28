"""services/maintenance: cookie validation sweep + profile refresh (7, 3.1, 5.3).

Traps covered: T-DB-15 (no session open across probes/sleeps), T-COOKIES-3
(a broken probe never invalidates cookies; the global inconclusive is
surfaced as cookie.validation_probe_failed), per-account isolation for the
48 h profile refresh.

Deterministic doubles: migrated real file SQLite database, an injected
probe_fn keyed on the cookie blob, a recorded no-op sleep_fn, an in-memory
event recorder. No network, no yt-dlp.
"""

import asyncio
import re
import sqlite3
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.config import Settings
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.models import Cookie, MonitoredAccount
from tikdown_rs.services import maintenance as maintenance_mod
from tikdown_rs.services.maintenance import (
    create_backup,
    refresh_all_profiles,
    validate_all_cookies,
)


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [e["event"] for e in self.events]


@pytest.fixture
async def factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Session factory over a REAL migrated file database (T-DATA-1 pattern)."""
    await asyncio.to_thread(run_migrations, tmp_path)
    db_engine = create_db_engine(sqlite_url_for(tmp_path))
    session_factory = make_session_factory(db_engine)
    yield session_factory
    await db_engine.dispose()


async def add_cookie(
    factory: async_sessionmaker[AsyncSession],
    label: str,
    blob: bytes,
    validation_state: str = "inconclusive",
) -> int:
    async with factory() as session:
        row = Cookie(label=label, cookie_blob=blob, validation_state=validation_state)
        session.add(row)
        await session.commit()
        return row.id


async def get_cookie(factory, cookie_id: int) -> Cookie:
    async with factory() as session:
        return await session.get(Cookie, cookie_id)


def probe_by_blob(good_marker: bytes):
    """probe_fn double: blobs containing good_marker verify, others fail transiently."""

    def probe(blob: bytes, url: str, max_entries: int) -> bool:
        if good_marker in blob:
            return True
        raise RuntimeError("429 service unavailable")

    return probe


# --- validate_all_cookies (7: sequential, 30-60 s between probes) ---


async def test_validate_all_cookies_counts_sleeps_and_emits(factory) -> None:
    good = await add_cookie(factory, "good", b"cookie good netscape")
    bad = await add_cookie(factory, "bad", b"cookie bad netscape")
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    recorder = Recorder()
    counts = await validate_all_cookies(
        factory,
        probe_urls=["u1"],
        probe_fn=probe_by_blob(b"good"),
        max_entries=5,
        on_event=recorder,
        sleep_fn=record_sleep,
    )
    assert counts == {"valid": 1, "invalid": 0, "inconclusive": 1}
    assert len(sleeps) == 1  # BETWEEN probes: probes - 1
    assert 30.0 <= sleeps[0] <= 60.0
    assert recorder.names() == [
        "cookie.validated",
        "cookie.validated",
        "cookie.validation_probe_failed",
    ]  # good: validated; bad: validated (verdict) + global probe_failed
    good_row = await get_cookie(factory, good)
    assert good_row.validation_state == "valid"
    assert good_row.last_validated_at is not None
    bad_row = await get_cookie(factory, bad)
    assert bad_row.validation_state == "inconclusive"  # T-COOKIES-3: untouched
    assert bad_row.last_validated_at is None


async def test_validate_all_cookies_skips_invalid_cookies(factory) -> None:
    invalid = await add_cookie(factory, "dead", b"cookie dead", validation_state="invalid")
    probed: list[bytes] = []

    def probe(blob: bytes, url: str, max_entries: int) -> bool:
        probed.append(blob)
        return True

    counts = await validate_all_cookies(
        factory,
        probe_urls=["u1"],
        probe_fn=probe,
        max_entries=5,
        sleep_fn=lambda _s: asyncio.sleep(0),
    )
    assert counts == {"valid": 0, "invalid": 0, "inconclusive": 0}
    assert probed == []  # an 'invalid' cookie is never re-probed
    row = await get_cookie(factory, invalid)
    assert row.validation_state == "invalid"


async def test_validate_all_cookies_empty_store(factory) -> None:
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    counts = await validate_all_cookies(
        factory,
        probe_urls=["u1"],
        probe_fn=probe_by_blob(b"good"),
        max_entries=5,
        sleep_fn=record_sleep,
    )
    assert counts == {"valid": 0, "invalid": 0, "inconclusive": 0}
    assert sleeps == []


# --- refresh_all_profiles (5.3: 48 h refresh) ---


class FakeProfileEngine:
    """extract_profile double: per-username profiles or raises."""

    def __init__(self, profiles: dict[str, dict], fail: set[str] | None = None) -> None:
        self.profiles = profiles
        self.fail = fail or set()
        self.calls: list[str] = []

    def extract_profile(self, username: str) -> dict:
        self.calls.append(username)
        if username in self.fail:
            raise RuntimeError("429 too many requests")
        return self.profiles[username]


async def add_account(factory, username: str, **overrides) -> int:
    async with factory() as session:
        account = MonitoredAccount(username=username, backfill_status="completed", **overrides)
        session.add(account)
        await session.commit()
        return account.id


async def get_account(factory, account_id: int) -> MonitoredAccount:
    async with factory() as session:
        return await session.get(MonitoredAccount, account_id)


async def test_refresh_all_profiles_persists_counters(factory) -> None:
    first = await add_account(factory, "alice", mode="monitor")
    second = await add_account(factory, "bob", mode="history")
    engine = FakeProfileEngine(
        {
            "alice": {
                "username": "alice",
                "followers": 10,
                "following_count": 20,
                "total_likes": 30,
                "video_count": 4,
            },
            "bob": {"username": "bob", "followers": 1, "video_count": 2},
        }
    )
    recorder = Recorder()
    refreshed = await refresh_all_profiles(factory, engine=engine, on_event=recorder)
    assert refreshed == 2
    alice = await get_account(factory, first)
    assert alice.follower_count == 10
    assert alice.following_count == 20
    assert alice.total_likes == 30
    assert alice.video_count == 4
    assert alice.profile_last_refreshed is not None
    bob = await get_account(factory, second)
    assert bob.follower_count == 1
    assert bob.video_count == 2
    assert recorder.names() == ["profile.refreshed", "profile.refreshed"]


async def test_refresh_all_profiles_skips_paused(factory) -> None:
    await add_account(factory, "active", mode="monitor")
    await add_account(factory, "resting", mode="monitor", paused=True)
    engine = FakeProfileEngine(
        {
            "active": {"username": "active", "followers": 5, "video_count": 1},
            "resting": {"username": "resting", "followers": 6, "video_count": 2},
        }
    )
    refreshed = await refresh_all_profiles(factory, engine=engine)
    assert refreshed == 1
    assert engine.calls == ["active"]


async def test_refresh_all_profiles_isolates_failing_account(factory) -> None:
    """A transient failure on one account never blocks the others."""
    good = await add_account(factory, "good", mode="monitor")
    await add_account(factory, "flaky", mode="monitor")
    engine = FakeProfileEngine(
        {"good": {"username": "good", "followers": 7, "video_count": 3}},
        fail={"flaky"},
    )
    refreshed = await refresh_all_profiles(factory, engine=engine)
    assert refreshed == 1
    row = await get_account(factory, good)
    assert row.follower_count == 7
    assert row.profile_last_refreshed is not None


async def test_refresh_all_profiles_preserves_absent_counters(factory) -> None:
    """M13: ProfileData only carries followers/video_count, so a profile dict
    lacking following_count/total_likes means 'not fetched' — the existing
    column values are preserved, never overwritten with NULL."""
    account_id = await add_account(
        factory, "carol", mode="monitor", following_count=11, total_likes=22
    )
    engine = FakeProfileEngine({"carol": {"username": "carol", "followers": 5, "video_count": 3}})
    refreshed = await refresh_all_profiles(factory, engine=engine)
    assert refreshed == 1
    row = await get_account(factory, account_id)
    assert row.follower_count == 5
    assert row.video_count == 3
    assert row.following_count == 11  # preserved
    assert row.total_likes == 22  # preserved


# --- create_backup (14.5: VACUUM INTO snapshot + retention, 15.2 rule 2) ---

SNAPSHOT_RE = re.compile(r"^tikdown-rs-\d{8}T\d{6}Z\.db$")


def _seed_backups(backups: Path, stamps: list[str]) -> None:
    """Fake OLDER snapshots with the exact naming pattern (newest last)."""
    backups.mkdir(parents=True, exist_ok=True)
    for stamp in stamps:
        (backups / f"tikdown-rs-{stamp}Z.db").write_bytes(b"old snapshot bytes")


async def test_create_backup_snapshot_is_valid_sqlite(factory, tmp_path: Path) -> None:
    """14.5: the snapshot is a readable SQLite DB and the seeded row round-trips."""
    settings = Settings(data_dir=tmp_path, system_backup_retain_count=7)
    cookie_id = await add_cookie(factory, "secret", b"cookie netscape")

    snapshot, deleted = await create_backup(factory, settings)

    assert SNAPSHOT_RE.match(snapshot.name)
    assert snapshot.exists() and snapshot.stat().st_size > 0
    assert deleted == 0
    con = sqlite3.connect(snapshot)
    try:
        assert con.execute("select count(*) from sqlite_master").fetchone()[0] > 0
        row = con.execute(
            "select label, cookie_blob from cookies where id = ?", (cookie_id,)
        ).fetchone()
    finally:
        con.close()
    assert row == ("secret", b"cookie netscape")


@pytest.mark.skipif(sys.platform == "win32", reason="Windows chmod only toggles read-only")
async def test_create_backup_file_mode_is_0600(factory, tmp_path: Path) -> None:
    """15.2 rule 2 / §14.5: cookies are stored unencrypted; the snapshot is a secret."""
    snapshot, _ = await create_backup(
        factory, Settings(data_dir=tmp_path, system_backup_retain_count=7)
    )
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600


async def test_create_backup_retention_keeps_newest(factory, tmp_path: Path) -> None:
    """14.5: only the SYSTEM_BACKUP_RETAIN_COUNT newest snapshots survive."""
    settings = Settings(data_dir=tmp_path, system_backup_retain_count=2)
    _seed_backups(tmp_path / "backups", ["20240101T000000", "20240102T000000", "20240103T000000"])

    snapshot, deleted = await create_backup(factory, settings)

    assert deleted == 2
    assert sorted(p.name for p in (tmp_path / "backups").iterdir()) == sorted(
        [snapshot.name, "tikdown-rs-20240103T000000Z.db"]
    )


async def test_create_backup_retention_never_deletes_unrelated_files(
    factory, tmp_path: Path
) -> None:
    """Retention only touches files matching the snapshot naming pattern."""
    settings = Settings(data_dir=tmp_path, system_backup_retain_count=1)
    backups = tmp_path / "backups"
    _seed_backups(backups, ["20240101T000000", "20240102T000000"])
    (backups / "operator-notes.txt").write_text("keep me")
    (backups / "tikdown-rs-old.db.bak").write_bytes(b"suffix breaks the pattern")

    _, deleted = await create_backup(factory, settings)

    assert deleted == 2
    assert (backups / "operator-notes.txt").exists()
    assert (backups / "tikdown-rs-old.db.bak").exists()


async def test_create_backup_same_second_overwrites_deterministically(
    factory, tmp_path: Path
) -> None:
    """Two invocations in the same second: the newer VACUUM INTO overwrites
    deterministically (staged + atomic os.replace), leaving exactly one snapshot
    and no *.db.tmp behind."""
    settings = Settings(data_dir=tmp_path, system_backup_retain_count=7)
    fixed = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

    first, _ = await create_backup(factory, settings, now_fn=lambda: fixed)
    (tmp_path / "backups" / first.name).write_bytes(b"stale partial content")
    second, _ = await create_backup(factory, settings, now_fn=lambda: fixed)

    assert first == second
    assert second.stat().st_size > 0  # real snapshot replaced the stale bytes
    assert sorted(p.name for p in (tmp_path / "backups").iterdir()) == [second.name]


async def test_create_backup_failure_preserves_previous_snapshot(
    factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3-001/R4-001: a failed VACUUM run must NOT destroy the last good snapshot.

    The pre-fix code unlinked the target before vacuuming, so a same-second
    collision plus a failure (disk full, interruption) erased the only
    known-good copy. With staged atomic publication the previous snapshot
    survives untouched and the failed stage leaves no *.db.tmp behind.
    """
    settings = Settings(data_dir=tmp_path, system_backup_retain_count=7)
    fixed = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    good, _ = await create_backup(factory, settings, now_fn=lambda: fixed)
    good_bytes = good.read_bytes()

    # Force the VACUUM statement to fail deterministically.
    monkeypatch.setattr(maintenance_mod, "text", lambda _q: sa_text("SELECT * FROM no_such_table"))
    with pytest.raises(OperationalError):
        await create_backup(factory, settings, now_fn=lambda: fixed)

    assert good.read_bytes() == good_bytes  # previous snapshot intact
    assert not (tmp_path / "backups" / f"{good.name}.tmp").exists()  # stage cleaned


async def test_stale_tmp_never_matches_retention_pattern(factory, tmp_path: Path) -> None:
    """R4-002: a staged *.db.tmp (partial/interrupted write) is invisible to
    retention -- only complete final-name snapshots are ever counted or evicted.
    """
    settings = Settings(data_dir=tmp_path, system_backup_retain_count=1)
    backups = tmp_path / "backups"
    _seed_backups(backups, ["20240101T000000", "20240102T000000"])
    (backups / "tikdown-rs-20250101T120000Z.db.tmp").write_bytes(b"partial write")

    snapshot, deleted = await create_backup(factory, settings)

    assert deleted == 2  # both older final-name snapshots evicted, tmp ignored
    assert SNAPSHOT_RE.match(snapshot.name)
    assert (backups / "tikdown-rs-20250101T120000Z.db.tmp").exists()  # untouched
    assert [p.name for p in backups.iterdir() if SNAPSHOT_RE.match(p.name)] == [
        snapshot.name,
    ]


def test_cli_backup_creates_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """10.1/14.5: `system backup` exits 0, prints the snapshot path + retention."""
    from typer.testing import CliRunner

    from tikdown_rs.cli.main import app

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    result = CliRunner().invoke(app, ["system", "backup"])

    assert result.exit_code == 0, result.output
    snapshots = list((tmp_path / "backups").glob("tikdown-rs-*.db"))
    assert len(snapshots) == 1
    assert str(snapshots[0]) in result.output
    assert "retention" in result.output


# --- B1 (T-ASYNC-8): blocking extract_profile must run OFF the loop thread ---


async def test_profile_refresh_runs_off_the_loop_thread(factory) -> None:
    """T-ASYNC-8/§1.1.3 (B1/JD-A-001): refresh must offload the blocking
    yt-dlp extraction; on-loop, the off-loop assertion raises and the account
    is skipped (refreshed stays 0)."""
    import asyncio as _asyncio

    await add_account(factory, "alice", mode="monitor")

    def _assert_off_loop() -> None:
        try:
            _asyncio.get_running_loop()
        except RuntimeError:
            return
        raise AssertionError("blocking yt-dlp extraction ran on the event loop thread")

    class OffLoopProfileEngine(FakeProfileEngine):
        def extract_profile(self, username: str) -> dict:
            _assert_off_loop()
            return self.profiles[username]

    engine = OffLoopProfileEngine({"alice": {"username": "alice", "followers": 7}})
    refreshed = await refresh_all_profiles(factory, engine=engine)
    assert refreshed == 1

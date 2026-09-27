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
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.models import Cookie, MonitoredAccount
from tikdown_rs.services.maintenance import refresh_all_profiles, validate_all_cookies


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

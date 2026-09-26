"""DownloadPacer + DownloadSemaphore tests. Reglas 3.5, 4.5.

Traps covered: T-DB-6 (immediate commit, fail-closed), T-DB-7 (millisecond
timespec), T-DB-12 (native ON CONFLICT bootstrap), T-ENGINE-8 (cooldown
disabled in tests via 0/0 overrides), T-ENGINE-26 (uniform draw per download,
rng injected). All deterministic: frozen clock, injected rng, file DB migrated
with run_migrations, no real-time dependence beyond bounded wait_for timeouts.
"""

import asyncio
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from tikdown_rs.core.config import Settings
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.core.pacing import DownloadPacer, DownloadSemaphore
from tikdown_rs.models import DownloadPacingState, reserve_download_slot

BASE = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def iso(dt: datetime) -> str:
    """Project-format timestamp: ISO8601 UTC, milliseconds timespec (T-DB-7)."""
    return dt.astimezone(UTC).isoformat(timespec="milliseconds")


class FrozenClock:
    """Callable returning a controllable datetime; injected as pacer._utcnow."""

    def __init__(self) -> None:
        self.now = BASE

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class SequenceRng:
    """Real injection double: uniform() replays a fixed value sequence."""

    def __init__(self, *values: float) -> None:
        self._values = list(values)
        self._i = 0

    def uniform(self, low: float, high: float) -> float:
        value = self._values[self._i % len(self._values)]
        self._i += 1
        return value


@pytest.fixture
async def migrated_factory(tmp_path: Path):
    await asyncio.to_thread(run_migrations, tmp_path)
    engine = create_db_engine(sqlite_url_for(tmp_path))
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


def make_pacer(factory, rng=None, min_seconds=None, max_seconds=None) -> DownloadPacer:
    """Settings stays env-valid; 0/0 and MAX<MIN exercise the pacer's own overrides."""
    return DownloadPacer(
        factory, Settings(), rng=rng, min_seconds=min_seconds, max_seconds=max_seconds
    )


async def read_row(factory) -> DownloadPacingState | None:
    async with factory() as session:
        return await session.get(DownloadPacingState, 1)


# --- 1. cooldown draw: injected rng drives next_allowed_at exactly ---


async def test_draw_advances_next_allowed_at_by_exact_drawn_value(migrated_factory) -> None:
    clock = FrozenClock()
    pacer = make_pacer(migrated_factory, rng=SequenceRng(37.5, 12.25))
    pacer._utcnow = clock  # type: ignore[method-assign]

    assert await pacer.reserve_next_slot() == 0.0
    row = await read_row(migrated_factory)
    assert row is not None
    assert row.next_allowed_at == iso(BASE + timedelta(seconds=37.5))

    # Busy at the same frozen instant: wait_seconds matches the row, no draw, no write.
    assert await pacer.reserve_next_slot() == 37.5
    row = await read_row(migrated_factory)
    assert row is not None
    assert row.next_allowed_at == iso(BASE + timedelta(seconds=37.5))

    # After the slot expires the SECOND draw, computed from the SECOND call time, applies.
    clock.advance(40.0)
    assert await pacer.reserve_next_slot() == 0.0
    row = await read_row(migrated_factory)
    assert row is not None
    assert row.next_allowed_at == iso(BASE + timedelta(seconds=52.25))


# --- 2. MIN == MAX -> fixed interval regardless of rng ---


async def test_min_equals_max_yields_fixed_interval(migrated_factory) -> None:
    clock = FrozenClock()
    pacer = make_pacer(
        migrated_factory, rng=__import__("random").Random(7), min_seconds=30, max_seconds=30
    )
    pacer._utcnow = clock  # type: ignore[method-assign]

    await pacer.reserve_next_slot()
    row = await read_row(migrated_factory)
    assert row is not None
    assert row.next_allowed_at == iso(BASE + timedelta(seconds=30))

    clock.advance(31.0)
    other = make_pacer(
        migrated_factory, rng=__import__("random").Random(999), min_seconds=30, max_seconds=30
    )
    other._utcnow = clock  # type: ignore[method-assign]
    await other.reserve_next_slot()
    row = await read_row(migrated_factory)
    assert row is not None
    assert row.next_allowed_at == iso(BASE + timedelta(seconds=61))


# --- 3. 0/0 -> disabled: immediate, no cooldown advance ---


async def test_zero_zero_disables_cooldown(migrated_factory) -> None:
    clock = FrozenClock()
    pacer = make_pacer(migrated_factory, rng=SequenceRng(999.0), min_seconds=0, max_seconds=0)
    pacer._utcnow = clock  # type: ignore[method-assign]

    await pacer.acquire()
    row = await read_row(migrated_factory)
    assert row is not None
    assert row.next_allowed_at == iso(BASE)

    # Repeated acquisitions at the same instant still go through immediately.
    await pacer.acquire()
    row = await read_row(migrated_factory)
    assert row is not None
    assert row.next_allowed_at == iso(BASE)


# --- 4. MAX < MIN -> ConfigurationError at construction ---


def test_max_below_min_raises_configuration_error(migrated_factory) -> None:
    # Settings itself already rejects MAX<MIN (pydantic validator); this checks
    # the pacer's own defense-in-depth against raw override arguments.
    with pytest.raises(ConfigurationError):
        DownloadPacer(migrated_factory, Settings(), min_seconds=50, max_seconds=10)


# --- 5. millisecond precision in the persisted timestamp ---


async def test_persisted_timestamp_has_three_fractional_digits(migrated_factory) -> None:
    clock = FrozenClock()
    pacer = make_pacer(migrated_factory, rng=SequenceRng(37.5))
    pacer._utcnow = clock  # type: ignore[method-assign]

    await pacer.reserve_next_slot()
    row = await read_row(migrated_factory)
    assert row is not None
    assert row.next_allowed_at is not None
    match = re.fullmatch(r"(.*)\.(\d{3})\+00:00", row.next_allowed_at)
    assert match is not None, row.next_allowed_at
    assert match.group(1) == "2026-01-01T00:00:37"


# --- 6. atomic reservation, two coroutines on one event loop ---


async def test_second_coroutine_waits_until_first_slot_expires(migrated_factory) -> None:
    clock = FrozenClock()
    pacer_a = make_pacer(migrated_factory, min_seconds=30, max_seconds=30)
    pacer_a._utcnow = clock  # type: ignore[method-assign]
    await pacer_a.acquire()  # slot now busy until BASE + 30s

    pacer_b = make_pacer(migrated_factory, rng=SequenceRng(15.0), min_seconds=10, max_seconds=60)
    pacer_b._utcnow = clock  # type: ignore[method-assign]
    pacer_b.max_single_sleep = 0.02
    task = asyncio.create_task(pacer_b.acquire())

    await asyncio.sleep(0.05)
    assert not task.done()
    clock.advance(10.0)
    await asyncio.sleep(0.05)
    assert not task.done()
    clock.advance(19.9)
    await asyncio.sleep(0.05)
    assert not task.done()  # clock at BASE + 29.9: still inside the held slot

    clock.advance(0.101)  # past BASE + 30s
    await asyncio.wait_for(task, timeout=2.0)
    row = await read_row(migrated_factory)
    assert row is not None
    # B's draw applied from the SECOND call time, not from A's.
    assert row.next_allowed_at == iso(BASE + timedelta(seconds=45.001))


# --- 7. atomic reservation, two PROCESSES (12.1 acceptance) ---


CHILD_CODE = """
import asyncio, sys
from datetime import datetime, timezone

from tikdown_rs.core.db import create_db_engine, make_session_factory
from tikdown_rs.models.download_pacing_state import read_next_allowed_at, reserve_download_slot


async def main(url: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    engine = create_db_engine(url)
    factory = make_session_factory(engine)
    async with factory() as session:
        # min=max=0: disabled draw, real CAS row update against the parent's slot.
        won = await reserve_download_slot(session, now=now, next_allowed=now)
        if won:
            print("WON")
        else:
            current = await read_next_allowed_at(session)
            print("BUSY " + (current or "?"))
    await engine.dispose()


asyncio.run(main(sys.argv[1]))
"""


async def test_cross_process_exactly_one_winner_per_contested_slot(tmp_path: Path) -> None:
    await asyncio.to_thread(run_migrations, tmp_path)
    url = sqlite_url_for(tmp_path)

    # Parent wins the contested slot first: raw CAS with an explicit slot 30s
    # ahead of REAL time, so the child's real-clock reservation must see busy.
    real_now = datetime.now(UTC)
    engine = create_db_engine(url)
    factory = make_session_factory(engine)
    async with factory() as session:
        assert await reserve_download_slot(
            session,
            now=iso(real_now),
            next_allowed=iso(real_now + timedelta(seconds=30)),
        )
    await engine.dispose()

    def run_child() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", CHILD_CODE, url],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    # Round 1: child must observe the future next_allowed_at and report busy.
    first = await asyncio.to_thread(run_child)
    assert first.returncode == 0, first.stderr
    assert first.stdout.startswith("BUSY "), first.stdout

    # Exactly one winner so far: parent won round 1, child saw busy.
    # Parent clears the row to the past, then the child's retry succeeds.
    engine = create_db_engine(url)
    factory = make_session_factory(engine)
    async with factory() as session:
        await session.execute(
            text("UPDATE download_pacing_state SET next_allowed_at = :past"),
            {"past": "2000-01-01T00:00:00.000+00:00"},
        )
        await session.commit()
    await engine.dispose()

    second = await asyncio.to_thread(run_child)
    assert second.returncode == 0, second.stderr
    assert second.stdout.startswith("WON"), second.stdout


# --- 8. fail-closed: future row blocks; absent row re-bootstraps with the draw ---


async def test_missing_row_bootstraps_and_still_fails_closed(migrated_factory) -> None:
    clock = FrozenClock()
    pacer = make_pacer(migrated_factory, rng=SequenceRng(15.0), min_seconds=10, max_seconds=60)
    pacer._utcnow = clock  # type: ignore[method-assign]
    pacer.max_single_sleep = 0.02

    # Future row set directly: acquire must NOT proceed immediately (fail closed).
    async with migrated_factory() as session:
        assert await reserve_download_slot(
            session, now=iso(BASE), next_allowed=iso(BASE + timedelta(seconds=30))
        )
    held = asyncio.create_task(pacer.acquire())
    await asyncio.sleep(0.05)
    assert not held.done()
    held.cancel()
    with pytest.raises(asyncio.CancelledError):
        await held

    # Absent row: acquire re-bootstraps and the draw applies (never fail-open).
    async with migrated_factory() as session:
        await session.execute(text("DELETE FROM download_pacing_state"))
        await session.commit()
    await asyncio.wait_for(pacer.acquire(), timeout=2.0)
    row = await read_row(migrated_factory)
    assert row is not None
    assert row.next_allowed_at == iso(BASE + timedelta(seconds=15.0))

    # The re-bootstrapped future slot blocks a second immediate acquire.
    blocked = asyncio.create_task(pacer.acquire())
    await asyncio.sleep(0.05)
    assert not blocked.done()
    clock.advance(15.001)
    await asyncio.wait_for(blocked, timeout=2.0)


# --- 9. DownloadSemaphore bounds concurrency ---


async def test_semaphore_with_limit_one_serializes() -> None:
    semaphore = DownloadSemaphore(1)
    release = asyncio.Event()
    entered_second = asyncio.Event()

    async def second() -> None:
        async with semaphore:
            entered_second.set()

    await semaphore.__aenter__()
    task = asyncio.create_task(second())
    await asyncio.sleep(0.05)
    assert not entered_second.is_set()
    assert not task.done()
    await semaphore.__aexit__(None, None, None)
    await asyncio.wait_for(task, timeout=2.0)
    assert entered_second.is_set()
    release.set()


async def test_semaphore_with_limit_two_admits_both() -> None:
    semaphore = DownloadSemaphore(2)
    entered = [asyncio.Event(), asyncio.Event()]

    async def occupant(index: int) -> None:
        async with semaphore:
            entered[index].set()
            await asyncio.sleep(0.05)

    tasks = [asyncio.create_task(occupant(i)) for i in range(2)]
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), timeout=2.0)
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=2.0)

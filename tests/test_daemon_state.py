"""daemon_state singleton mutators: internal commits, upserts, stop refusal.

Traps covered: T-DB-3, T-DB-12, T-DB-13, T-CLI-6. Rules: 3.5, 3.7, 5.3.
"""

from datetime import datetime

import pytest

from tikdown_rs.core.daemon_state import (
    _upsert,
    clear_stop_requested,
    read_status,
    read_stop_requested,
    record_selfcheck,
    register_daemon_start,
    request_daemon_stop,
    write_heartbeat,
)
from tikdown_rs.core.db import create_db_engine, make_session_factory
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.models import Base
from tikdown_rs.models.daemon_state import DaemonState, ensure_daemon_state_row

IN_MEMORY_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def session_factory():
    """In-memory engine via core/db.py with schema and the singleton row ensured."""
    engine = create_db_engine(IN_MEMORY_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = make_session_factory(engine)
    async with factory() as session:
        await ensure_daemon_state_row(session)
    yield factory
    await engine.dispose()


async def test_write_heartbeat_persists_iso_utc_and_commits(session_factory) -> None:
    """T-DB-13: write_heartbeat commits internally; visible from a second session."""
    async with session_factory() as session:
        await write_heartbeat(session, pid=4242)

    async with session_factory() as second_session:
        row = await second_session.get(DaemonState, 1)
    assert row is not None
    assert row.last_heartbeat_at is not None
    parsed = datetime.fromisoformat(row.last_heartbeat_at)
    assert parsed.tzinfo is not None  # ISO8601 with explicit UTC offset
    assert row.daemon_pid == 4242


async def test_set_and_clear_stop_requested_round_trip(session_factory) -> None:
    """stop_requested set/clear round trip (set path = upsert, as the in-process writer)."""
    async with session_factory() as session:
        assert not await read_stop_requested(session)
        await session.execute(_upsert({"stop_requested": True}))
        await session.commit()
        assert await read_stop_requested(session)
        await clear_stop_requested(session)
        assert not await read_stop_requested(session)


async def test_clear_stop_requested_works_when_row_absent() -> None:
    """T-DB-12: upsert pattern; an absent singleton row is not an error."""
    engine = create_db_engine(IN_MEMORY_URL)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)  # empty table, no row
        factory = make_session_factory(engine)
        async with factory() as session:
            await clear_stop_requested(session)
            assert not await read_stop_requested(session)
    finally:
        await engine.dispose()


async def test_request_daemon_stop_refuses_when_no_daemon_pid(session_factory) -> None:
    """T-CLI-6 failure mode: writing a flag nobody reads is refused (daemon_pid NULL)."""
    async with session_factory() as session:
        with pytest.raises(ConfigurationError, match="daemon_pid"):
            await request_daemon_stop(session)
        # Refusal must not have written the flag.
        assert not await read_stop_requested(session)


async def test_request_daemon_stop_sets_flag_when_running(session_factory) -> None:
    async with session_factory() as session:
        await register_daemon_start(session, pid=999)
        await request_daemon_stop(session)
        assert await read_stop_requested(session)


async def test_record_selfcheck_ok_persists_null_reason(session_factory) -> None:
    """T-DB-13: record_selfcheck commits internally; visible from a second session."""
    async with session_factory() as session:
        await record_selfcheck(session, ok=True, degraded_reason=None)

    async with session_factory() as second_session:
        row = await second_session.get(DaemonState, 1)
    assert row is not None
    assert row.last_selfcheck_ok is True
    assert row.degraded_reason is None
    parsed = datetime.fromisoformat(row.last_selfcheck_at)
    assert parsed.tzinfo is not None  # ISO8601 with explicit UTC offset


async def test_record_selfcheck_degraded_persists_reason(session_factory) -> None:
    async with session_factory() as session:
        await record_selfcheck(
            session, ok=False, degraded_reason="impersonation: curl_cffi-missing"
        )

    async with session_factory() as second_session:
        row = await second_session.get(DaemonState, 1)
    assert row.last_selfcheck_ok is False
    assert row.degraded_reason == "impersonation: curl_cffi-missing"


async def test_read_status_returns_row_or_none(session_factory) -> None:
    async with session_factory() as session:
        assert await read_status(session) is not None
        await register_daemon_start(session, pid=7)
    # Visibility after commit: a fresh reader session sees the registered pid.
    async with session_factory() as session:
        row = await read_status(session)
    assert row is not None
    assert row.daemon_pid == 7

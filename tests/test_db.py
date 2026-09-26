"""Tests for core.db: PRAGMA order (T-DB-5), in-memory URL detection (T-DB-9).

Regla: §3.7, §13.1.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.pool import NullPool, StaticPool

import tikdown_rs.core.db as db_module
from tikdown_rs.core.db import (
    PRAGMA_STATEMENTS,
    apply_pragmas,
    create_db_engine,
    is_in_memory_url,
    make_session_factory,
    sqlite_url_for,
)
from tikdown_rs.models import Base
from tikdown_rs.models.daemon_state import DaemonState, ensure_daemon_state_row


class RecordingCursor:
    """Fake DBAPI cursor recording executed statements in order (full signature)."""

    def __init__(self, executed: list[str]) -> None:
        self._executed = executed

    def execute(self, statement: str, parameters: object = None) -> None:
        self._executed.append(statement)

    def close(self) -> None:
        return None


class RecordingConnection:
    """Fake DBAPI connection handing out recording cursors."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self.executed)


def test_pragma_order_busy_timeout_first_then_wal() -> None:
    """T-DB-5: busy_timeout must execute first, journal_mode=WAL second."""
    connection = RecordingConnection()
    apply_pragmas(connection)
    assert connection.executed[0] == "PRAGMA busy_timeout=5000"
    assert connection.executed[1] == "PRAGMA journal_mode=WAL"
    assert connection.executed == list(PRAGMA_STATEMENTS)


async def test_engine_connect_executes_pragma_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every new connection routes through apply_pragmas (the T-DB-5 hook)."""
    real_apply = db_module.apply_pragmas
    calls: list[object] = []

    def spy(connection: object, **kwargs: object) -> None:
        calls.append(connection)
        real_apply(connection)

    monkeypatch.setattr(db_module, "apply_pragmas", spy)
    engine = create_db_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.connect():
            pass
    finally:
        await engine.dispose()
    assert len(calls) >= 1


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("sqlite+aiosqlite:///:memory:", True),
        ("sqlite+aiosqlite://", True),
        ("sqlite+aiosqlite:///C:/x/y.db", False),
    ],
)
def test_is_in_memory_url(url: str, expected: bool) -> None:
    """T-DB-9: decision comes from the parsed URL, never from text patterns."""
    assert is_in_memory_url(url) is expected


def test_file_url_is_not_in_memory(tmp_path: object) -> None:
    url = sqlite_url_for(tmp_path)  # type: ignore[arg-type]
    assert is_in_memory_url(url) is False


async def test_pool_policy_static_for_memory_null_for_file(tmp_path: object) -> None:
    data_dir = tmp_path  # type: ignore[assignment]
    mem_engine = create_db_engine("sqlite+aiosqlite:///:memory:")
    file_engine = create_db_engine(sqlite_url_for(data_dir))
    try:
        assert isinstance(mem_engine.sync_engine.pool, StaticPool)
        assert isinstance(file_engine.sync_engine.pool, NullPool)
    finally:
        await mem_engine.dispose()
        await file_engine.dispose()


async def test_pragma_effect_on_real_connection(tmp_path: object) -> None:
    """PRAGMAs actually apply: foreign_keys ON and busy_timeout 5000 on a live connection."""
    engine = create_db_engine(sqlite_url_for(tmp_path))  # type: ignore[arg-type]
    try:
        async with engine.connect() as connection:
            foreign_keys = (await connection.execute(text("PRAGMA foreign_keys"))).scalar()
            busy_timeout = (await connection.execute(text("PRAGMA busy_timeout"))).scalar()
    finally:
        await engine.dispose()
    assert foreign_keys == 1
    assert busy_timeout == 5000


def test_sqlite_url_for_builds_file_url(tmp_path: object) -> None:
    url = sqlite_url_for(tmp_path)  # type: ignore[arg-type]
    assert url.startswith("sqlite+aiosqlite:///")
    assert url.endswith("tikdown-rs.db")
    # T-DEPLOY-22: never compare/emit separator-dependent strings.
    assert "\\" not in url


async def test_session_factory_yields_working_sessions() -> None:
    engine = create_db_engine("sqlite+aiosqlite:///:memory:")
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1
    finally:
        await engine.dispose()


async def test_ensure_daemon_state_row_is_idempotent(tmp_path: object) -> None:
    """T-DB-6/T-DB-11/T-DB-12/T-DB-13: native upsert, immediate commit, single row."""
    engine = create_db_engine(sqlite_url_for(tmp_path))  # type: ignore[arg-type]
    factory = make_session_factory(engine)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with factory() as session:
            row = await ensure_daemon_state_row(session)
            assert row.id == 1
            assert row.monitor_running is False
            assert row.stop_requested is False
            assert row.db_busy_count_5min == 0
            assert row.downloads_paused is False

        # Second call must not raise and must not duplicate the singleton.
        async with factory() as session:
            await ensure_daemon_state_row(session)

        async with engine.connect() as connection:
            count = (await connection.execute(text("SELECT COUNT(*) FROM daemon_state"))).scalar()
        assert count == 1
    finally:
        await engine.dispose()


def test_daemon_state_model_shapes_singleton_table() -> None:
    """T-DB-12/T-DB-11 support: CHECK(id=1) and the expected column set exist."""
    table = DaemonState.__table__
    assert table.name == "daemon_state"
    constraints = {c.name for c in table.constraints}
    assert "ck_daemon_state_singleton" in constraints
    expected_columns = {
        "id",
        "monitor_running",
        "stop_requested",
        "daemon_pid",
        "daemon_started_at",
        "last_heartbeat_at",
        "db_busy_count_5min",
        "downloads_paused",
        "last_known_good_ytdlp_version",
        "last_selfcheck_at",
        "last_selfcheck_ok",
    }
    assert set(table.columns.keys()) == expected_columns

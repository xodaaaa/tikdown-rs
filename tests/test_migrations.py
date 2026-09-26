"""Tests for core.migrations: §5.4 decision tree, cross-process lock, config location.

Trampas covered: T-DEPLOY-1, T-DEPLOY-3, T-DEPLOY-4, T-DB-10. Regla: §13.1.
"""

import sqlite3
import sys
import threading
from pathlib import Path
from typing import IO

import pytest

import tikdown_rs.core.migrations as migrations_module
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.core.migrations import (
    MARKER_TABLE_REVISION,
    locate_alembic_config,
    run_migrations,
)

# DDL copied from alembic/versions/0001_create_daemon_state.py (column set must match).
MARKER_TABLE_DDL = """
CREATE TABLE daemon_state (
    id INTEGER NOT NULL,
    monitor_running BOOLEAN NOT NULL DEFAULT 0,
    stop_requested BOOLEAN NOT NULL DEFAULT 0,
    daemon_pid INTEGER,
    daemon_started_at TEXT,
    last_heartbeat_at TEXT,
    db_busy_count_5min INTEGER NOT NULL DEFAULT 0,
    downloads_paused BOOLEAN NOT NULL DEFAULT 0,
    last_known_good_ytdlp_version TEXT,
    last_selfcheck_at TEXT,
    last_selfcheck_ok BOOLEAN,
    PRIMARY KEY (id),
    CONSTRAINT ck_daemon_state_singleton CHECK (id = 1)
)
"""


def query(data_dir: Path, sql: str) -> list[tuple]:
    connection = sqlite3.connect(data_dir / "tikdown-rs.db")
    try:
        return list(connection.execute(sql))
    finally:
        connection.close()


def table_names(data_dir: Path) -> set[str]:
    return {row[0] for row in query(data_dir, "SELECT name FROM sqlite_master WHERE type='table'")}


def _acquire_exclusive_lock(lock_path: Path) -> IO[str]:
    """Hold the OS-level lock the way a second process would (T-DEPLOY-3)."""
    # Held across function return on purpose (the lock must outlive this call).
    handle = open(lock_path, "a+b")  # noqa: SIM115
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def test_fresh_db_migrates_and_inserts_nothing(tmp_path: Path) -> None:
    run_migrations(tmp_path)
    tables = table_names(tmp_path)
    assert "daemon_state" in tables
    assert "alembic_version" in tables
    assert query(tmp_path, "SELECT version_num FROM alembic_version") == [(MARKER_TABLE_REVISION,)]
    # T-DB-6: the migration creates the table but NEVER inserts the singleton row.
    assert query(tmp_path, "SELECT COUNT(*) FROM daemon_state") == [(0,)]


def test_rerun_never_restamps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-DEPLOY-1: with alembic_version present, plain upgrade only; stamp never fires."""
    stamp_calls: list[str] = []
    real_stamp = migrations_module.command.stamp

    def stamp_spy(config: object, revision: object, **kwargs: object) -> None:
        stamp_calls.append(str(revision))
        real_stamp(config, revision)  # type: ignore[arg-type]

    monkeypatch.setattr(migrations_module.command, "stamp", stamp_spy)
    run_migrations(tmp_path)
    run_migrations(tmp_path)
    assert stamp_calls == []
    assert query(tmp_path, "SELECT version_num FROM alembic_version") == [(MARKER_TABLE_REVISION,)]


def test_foreign_tables_db_gets_plain_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§5.4: a foreign-tables DB without marker table is a fresh DB: upgrade, no stamp."""
    connection = sqlite3.connect(tmp_path / "tikdown-rs.db")
    try:
        connection.execute("CREATE TABLE foreign_stuff (value TEXT)")
        connection.commit()
    finally:
        connection.close()

    stamp_calls: list[str] = []
    real_stamp = migrations_module.command.stamp

    def stamp_spy(config: object, revision: object, **kwargs: object) -> None:
        stamp_calls.append(str(revision))
        real_stamp(config, revision)  # type: ignore[arg-type]

    monkeypatch.setattr(migrations_module.command, "stamp", stamp_spy)
    run_migrations(tmp_path)
    tables = table_names(tmp_path)
    assert "daemon_state" in tables
    assert "foreign_stuff" in tables
    assert stamp_calls == []


def test_marker_table_without_version_stamps_marker_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-DB-10: pre-existing marker table stamps MARKER_TABLE_REVISION (never "head") + upgrade."""
    connection = sqlite3.connect(tmp_path / "tikdown-rs.db")
    try:
        connection.execute(MARKER_TABLE_DDL)
        connection.commit()
    finally:
        connection.close()

    stamp_calls: list[str] = []
    real_stamp = migrations_module.command.stamp

    def stamp_spy(config: object, revision: object, **kwargs: object) -> None:
        stamp_calls.append(str(revision))
        real_stamp(config, revision)  # type: ignore[arg-type]

    monkeypatch.setattr(migrations_module.command, "stamp", stamp_spy)
    run_migrations(tmp_path)
    assert stamp_calls == [MARKER_TABLE_REVISION]
    assert query(tmp_path, "SELECT version_num FROM alembic_version") == [(MARKER_TABLE_REVISION,)]


def test_cross_process_lock_blocks_concurrent_migration(tmp_path: Path) -> None:
    """T-DEPLOY-3: run_migrations blocks while another holder owns .migrate.lock."""
    handle = _acquire_exclusive_lock(tmp_path / ".migrate.lock")
    try:
        done = threading.Event()
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                run_migrations(tmp_path)
            except Exception as exc:  # noqa: BLE001 - failure transport, not a swallow
                errors.append(exc)
            finally:
                done.set()

        thread = threading.Thread(target=worker)
        thread.start()
        # Bounded wait to observe "does not complete": a correct implementation stays
        # blocked while the lock is held, so the timeout outcome is deterministic.
        thread.join(timeout=1.0)
        assert thread.is_alive(), "run_migrations ignored the cross-process migration lock"
        handle.close()  # release: the blocked migration must now proceed
        thread.join(timeout=30)
        assert done.is_set()
        assert errors == []
        assert "daemon_state" in table_names(tmp_path)
    finally:
        handle.close()


def test_locate_alembic_config_missing_raises_with_candidates(tmp_path: Path) -> None:
    """T-DEPLOY-4: explicit error listing every probed candidate."""
    candidates = (tmp_path / "nope" / "alembic.ini", tmp_path / "also-nope" / "alembic.ini")
    with pytest.raises(ConfigurationError) as excinfo:
        locate_alembic_config(candidates)
    message = str(excinfo.value)
    assert str(candidates[0]) in message
    assert str(candidates[1]) in message


def test_locate_alembic_config_finds_repo_ini() -> None:
    """Default candidate probing resolves the dev/editable checkout's alembic.ini."""
    assert locate_alembic_config().name == "alembic.ini"


def test_marker_revision_constant_matches_migration_id() -> None:
    assert MARKER_TABLE_REVISION == "0001_daemon_state"

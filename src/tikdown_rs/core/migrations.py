"""Idempotent Alembic migration runner for the file-based SQLite database.

Trampas neutralizadas: T-DEPLOY-1, T-DEPLOY-3, T-DEPLOY-4, T-DB-10. Regla: §5.4.
"""

import logging
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic.config import Config

from alembic import command
from tikdown_rs.core.db import DB_FILE_NAME, sqlite_url_for
from tikdown_rs.core.errors import ConfigurationError

logger = logging.getLogger(__name__)

MARKER_TABLE_REVISION = "0001_daemon_state"

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


@contextmanager
def _migration_lock(data_dir: Path) -> Iterator[None]:
    """Exclusive cross-process lock on <data_dir>/.migrate.lock (T-DEPLOY-3)."""
    lock_path = data_dir / ".migrate.lock"
    with lock_path.open("a+b") as handle:
        if sys.platform == "win32":
            # ponytail: msvcrt LK_LOCK retries ~10s then raises; switch to a poll loop
            # if a competing holder can outlive that window.
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _default_config_candidates() -> tuple[Path, ...]:
    """Editable/dev checkout first (next to the package source), then cwd (image)."""
    return (
        Path(__file__).resolve().parents[3] / "alembic.ini",
        Path.cwd() / "alembic.ini",
    )


def locate_alembic_config(candidates: tuple[Path, ...] | None = None) -> Path:
    """Resolve alembic.ini by probing candidates with an explicit error (T-DEPLOY-4).

    Never a bare Path(__file__).parents[1]: a wheel install points at site-packages,
    where no alembic.ini exists.
    """
    probed = candidates if candidates is not None else _default_config_candidates()
    for candidate in probed:
        if candidate.is_file():
            return candidate
    raise ConfigurationError(
        "alembic.ini not found; probed candidates: "
        + ", ".join(str(candidate) for candidate in probed)
    )


def _db_state(db_path: Path) -> tuple[bool, bool]:
    """Return (has_alembic_version, has_marker_table) for the target database."""
    if not db_path.exists():
        return (False, False)
    connection = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()
    return ("alembic_version" in tables, "daemon_state" in tables)


def run_migrations(data_dir: Path) -> None:
    """Run migrations idempotently under the cross-process lock (§5.4 decision tree).

    Decision tree:
    - no alembic_version and no marker table -> plain upgrade (foreign DB == fresh DB).
    - no alembic_version but marker table exists -> stamp(MARKER_TABLE_REVISION) then
      upgrade("head") in the same operation (T-DB-10: never stamp "head").
    - alembic_version present (any value) -> plain upgrade; never re-stamp (T-DEPLOY-1).
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    alembic_ini = locate_alembic_config()
    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(alembic_ini.parent / "alembic"))
    config.set_main_option("sqlalchemy.url", sqlite_url_for(data_dir))
    db_path = data_dir / DB_FILE_NAME

    with _migration_lock(data_dir):
        has_version, has_marker = _db_state(db_path)
        if not has_version and has_marker:
            logger.warning(
                "Found marker table without alembic_version; stamping %s then upgrading",
                MARKER_TABLE_REVISION,
            )
            command.stamp(config, MARKER_TABLE_REVISION)
            command.upgrade(config, "head")
        else:
            command.upgrade(config, "head")

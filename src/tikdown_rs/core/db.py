"""Async SQLite engine and session factory with per-connection PRAGMA setup.

Trampas neutralizadas: T-DB-5, T-DB-9. Regla: §3.7.
"""

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool

DB_FILE_NAME = "tikdown-rs.db"

# T-DB-5: the order is mandatory: busy_timeout FIRST, journal_mode=WAL second, the rest after.
PRAGMA_STATEMENTS: tuple[str, ...] = (
    "PRAGMA busy_timeout=5000",
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA temp_store=MEMORY",
    "PRAGMA cache_size=-65536",
    "PRAGMA mmap_size=268435456",
)


def is_in_memory_url(url: str) -> bool:
    """Decide in-memory from the parsed URL only (empty database or :memory:), T-DB-9.

    Never substring-search "///" in the URL text: sqlite+aiosqlite:///:memory: carries
    "///" and IS in memory.
    """
    database = make_url(url).database or ""
    return database in ("", ":memory:")


def apply_pragmas(dbapi_connection: object) -> None:
    """Execute the mandatory PRAGMA sequence, in order (T-DB-5).

    Works on the raw DBAPI connection handed to the sync-engine "connect" event
    (SQLAlchemy adapts aiosqlite connections to a sync facade there).
    """
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        for statement in PRAGMA_STATEMENTS:
            cursor.execute(statement)
    finally:
        cursor.close()


def _register_pragma_listener(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: object, connection_record: object) -> None:
        apply_pragmas(dbapi_connection)


def create_db_engine(url: str) -> AsyncEngine:
    """Build the async engine: StaticPool for in-memory URLs (tests), NullPool for files."""
    pool_class = StaticPool if is_in_memory_url(url) else NullPool
    engine = create_async_engine(url, poolclass=pool_class)
    _register_pragma_listener(engine)
    return engine


def sqlite_url_for(data_dir: Path) -> str:
    """Build the aiosqlite URL for the project database inside data_dir."""
    return f"sqlite+aiosqlite:///{(data_dir / DB_FILE_NAME).as_posix()}"


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Short, explicit sessions (T-DB-15); attributes stay readable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)

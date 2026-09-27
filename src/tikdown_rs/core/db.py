"""Async SQLite engine and session factory with per-connection PRAGMA setup.

Trampas neutralizadas: T-DB-5, T-DB-9, T-DB-14. Reglas: §3.7, §5.1, §5.6.
"""

import asyncio
import collections
import time
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
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


# --- 5.6 contention observability: IN MEMORY, per process (T-DB-14) ---

#: Real rotating window: 'database is locked' timestamps from the LAST 5 min.
CONTENTION_WINDOW_SECONDS = 300.0
_locked_at: collections.deque[float] = collections.deque()


def is_db_locked_message(message: str) -> bool:
    """True when the message is the classic SQLite busy error (5.6)."""
    return "database is locked" in message


def record_db_locked_error(message: str) -> bool:
    """Count one 'database is locked' occurrence into the 5-min window (5.6).

    Returns True only when the message actually was a lock error. The window
    is pruned lazily on read (heartbeat) and on write; a process restart
    resets it, which is exactly the per-process semantics the spec wants.
    """
    if not is_db_locked_message(message):
        return False
    now = time.monotonic()
    _locked_at.append(now)
    while _locked_at and now - _locked_at[0] > CONTENTION_WINDOW_SECONDS:
        _locked_at.popleft()
    return True


def db_busy_count_5min() -> int:
    """Locked errors seen in the last 5 REAL minutes (lazily pruned, 5.6)."""
    now = time.monotonic()
    while _locked_at and now - _locked_at[0] > CONTENTION_WINDOW_SECONDS:
        _locked_at.popleft()
    return len(_locked_at)


def reset_contention_window() -> None:
    """Test helper: clear the process-wide window."""
    _locked_at.clear()


def _register_contention_listener(engine: AsyncEngine) -> None:
    """Engine-level handle_error listener feeding the 5.6 counter (T-DB-14)."""

    @event.listens_for(engine.sync_engine, "handle_error")
    def _on_handle_error(exception_context: object) -> None:
        exception = getattr(exception_context, "sqlalchemy_exception", None) or getattr(
            exception_context, "original_exception", None
        )
        if exception is not None:
            record_db_locked_error(str(exception))


async def retry_on_locked(fn, *, attempts: int = 5, base_delay: float = 0.05) -> None:
    """Bounded exponential-backoff retry for 'database is locked' (§5.1 startup).

    The one-time delete->WAL journal conversion takes an exclusive lock that
    busy_timeout cannot ride out; a fresh DB under concurrent openers needs a
    bounded retry, not a crash (M2/M3 flake action). Total sleep is bounded:
    base_delay * (2**attempts - 1); non-lock errors re-raise immediately.
    """
    for attempt in range(attempts):
        try:
            return await fn()
        except OperationalError as exc:
            if not is_db_locked_message(str(exc)) or attempt >= attempts - 1:
                raise
            await asyncio.sleep(base_delay * (2**attempt))


def create_db_engine(url: str) -> AsyncEngine:
    """Build the async engine: StaticPool for in-memory URLs (tests), NullPool for files."""
    pool_class = StaticPool if is_in_memory_url(url) else NullPool
    engine = create_async_engine(url, poolclass=pool_class)
    _register_pragma_listener(engine)
    _register_contention_listener(engine)  # 5.6, T-DB-14
    return engine


def sqlite_url_for(data_dir: Path) -> str:
    """Build the aiosqlite URL for the project database inside data_dir."""
    return f"sqlite+aiosqlite:///{(data_dir / DB_FILE_NAME).as_posix()}"


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Short, explicit sessions (T-DB-15); attributes stay readable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)

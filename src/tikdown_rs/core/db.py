"""Conexión a la base de datos SQLite (WAL) — core/db.py.

story: e01s04
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

LOG = logging.getLogger("tikdown_rs.db")

# Contador en memoria de contención SQLite (se persiste en daemon_state, §5.8)
_busy_count: int = 0


def _ensure_parent_dir(db_path: str) -> None:
    """Crea el directorio padre de la DB si no existe (L-C9).

    Chequeo estructural ('///' not in url), nunca el literal ':memory:'.
    """
    if "///" not in db_path:
        return  # URL de memoria
    path_str = db_path.split("///", 1)[1]
    parent = Path(path_str).parent
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


def create_async_engine_wal(db_url: str) -> AsyncEngine:
    """Crea el engine async SQLite con WAL, PRAGMA order (L-C5) y NullPool."""
    _ensure_parent_dir(db_url)

    engine = create_async_engine(db_url, poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        # ORDEN OBLIGATORIO (L-C5): busy_timeout PRIMERO, journal_mode DESPUÉS.
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    @event.listens_for(engine.sync_engine, "handle_error")
    def _handle_busy(context):  # noqa: ANN001
        global _busy_count
        exc = context.original_exception
        if isinstance(exc, OperationalError) and "database is locked" in str(exc):
            _busy_count += 1
            LOG.warning("db.busy_timeout", extra={"db_busy_count": _busy_count})
        return None

    return engine


def busy_count() -> int:
    """Contador de contención en memoria (persistido en daemon_state por el daemon)."""
    return _busy_count

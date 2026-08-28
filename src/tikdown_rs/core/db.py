"""Conexión a la base de datos SQLite (WAL) — core/db.py.

story: e01s04 e02s04
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

LOG = logging.getLogger("tikdown_rs.db")

# Contención SQLite con ventana rotativa real de 5 min (§5.8): marcas de tiempo,
# no un contador acumulado. El heartbeat del daemon lo persiste en
# daemon_state.db_busy_count_5min; el CLI lo lee SIEMPRE desde daemon_state,
# nunca del proceso CLI propio (T19).
_busy_timestamps: list[float] = []
_WINDOW_SECONDS = 300  # 5 minutos


def record_busy() -> None:
    """Registra un evento de contención SQLite con marca de tiempo (§5.8)."""
    _busy_timestamps.append(time.time())


def busy_count() -> int:
    """Contador de contención en la ventana rotativa de 5 min (§5.8).

    Descarta entradas fuera de la ventana antes de contar.
    """
    cutoff = time.time() - _WINDOW_SECONDS
    while _busy_timestamps and _busy_timestamps[0] < cutoff:
        _busy_timestamps.pop(0)
    return len(_busy_timestamps)


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
        exc = context.original_exception
        if isinstance(exc, OperationalError) and "database is locked" in str(exc):
            record_busy()
            LOG.warning("db.busy_timeout", extra={"db_busy_count": busy_count()})
        return None

    return engine

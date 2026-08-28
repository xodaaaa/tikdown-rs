"""e01s04 — Esquema, PRAGMAs, directorio padre y singleton idempotente."""

# story: e01s04
import asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tikdown_rs.core.daemon_state import get_or_create_daemon_state
from tikdown_rs.core.db import create_async_engine_wal
from tikdown_rs.models.models import Base, DaemonState


def test_ensure_parent_dir_crea_directorio(tmp_path):
    """L-C9: la DB crea su directorio padre si no existe."""
    from tikdown_rs.core.db import _ensure_parent_dir

    db_file = tmp_path / "nested" / "dir" / "test.db"
    _ensure_parent_dir(f"sqlite+aiosqlite:///{db_file}")
    assert db_file.parent.exists()


async def test_wal_journal_mode(tmp_path):
    """WAL activado: PRAGMA journal_mode devuelve 'wal'."""
    db_file = tmp_path / "data" / "test.db"
    engine = create_async_engine_wal(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA journal_mode"))
        mode = result.scalar_one()
    assert mode == "wal"
    await engine.dispose()


async def test_singleton_idempotente_concurrente(tmp_path):
    """T17/L-C6: get_or_create_daemon_state es idempotente bajo concurrencia.

    Dos corrutinas crean el singleton a la vez; solo una fila persiste.
    """
    db_file = tmp_path / "singleton.db"
    engine = create_async_engine_wal(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _create() -> int:
        async with maker() as s:
            row = await get_or_create_daemon_state(s)
            return row.id

    ids = await asyncio.gather(_create(), _create())
    assert ids == [1, 1]

    # Solo una fila
    async with maker() as s:
        result = await s.execute(select(DaemonState))
        rows = result.scalars().all()
    assert len(rows) == 1
    await engine.dispose()

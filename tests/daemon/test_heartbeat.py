"""e02s04 — heartbeat persistido + contención con ventana rotativa (§5.8)."""
# story: e02s04
import time

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.core.daemon_state import update_heartbeat
from tikdown_rs.core.db import busy_count, record_busy
from tikdown_rs.models.models import Base, DaemonState


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_heartbeat_persiste(maker):
    """update_heartbeat persiste last_heartbeat_at y daemon_pid (T37)."""
    async with maker() as s:
        await update_heartbeat(s, pid=1234)
    async with maker() as s:
        row = (await s.execute(select(DaemonState))).scalar_one()
        assert row.last_heartbeat_at is not None
        assert row.daemon_pid == 1234


def test_busy_count_ventana_rotativa():
    """§5.8: record_busy incrementa; la ventana de 5 min descarta entradas viejas."""
    before = busy_count()
    record_busy()
    assert busy_count() > before


def test_busy_count_ventana_descarta_viejas():
    """§5.8: entradas fuera de la ventana de 5 min no cuentan."""
    from tikdown_rs.core.db import _busy_timestamps

    _busy_timestamps.clear()
    # Simular una entrada vieja (>5 min)
    _busy_timestamps.append(time.time() - 400)
    count = busy_count()
    assert count == 0

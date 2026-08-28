"""e02s03 — runner: arranque, helpers commit interno (T37), monitor detenido (T5.1)."""

# story: e02s03
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.core.daemon_state import set_monitor_running, set_stop_requested
from tikdown_rs.models.models import Base, DaemonState


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_set_stop_requested_commitea_interno(maker):
    """T37: el helper mutador commitea internamente (no depende del llamador)."""
    async with maker() as s:
        await set_stop_requested(s, True)
    # Nueva sesión: el cambio PERSISTE (commit interno)
    async with maker() as s:
        row = (await s.execute(select(DaemonState))).scalar_one()
        assert row.stop_requested is True


async def test_set_monitor_running_commitea_interno(maker):
    """T37: set_monitor_running commitea internamente."""
    async with maker() as s:
        await set_monitor_running(s, True)
    async with maker() as s:
        row = (await s.execute(select(DaemonState))).scalar_one()
        assert row.monitor_running is True


async def test_monitor_autostart_false_default():
    """T5.1: el monitor arranca detenido por defecto (Settings default)."""
    from tikdown_rs.core.config import Settings

    s = Settings(_env_file=None)
    assert s.monitor_autostart is False

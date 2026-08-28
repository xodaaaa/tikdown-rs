"""e03s01 — services/accounts: CRUD, notify, check (T20/T60/L-G3)."""
# story: e03s01
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.models.models import Base, MonitoredAccount
from tikdown_rs.services import accounts


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_add_cuenta_history_default(maker):
    """add: username sin @, mode history por defecto."""
    async with maker() as s:
        await accounts.add(s, "usuario")
    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        assert row.username == "usuario"
        assert row.mode == "history"
        assert row.monitor_after_backfill is False


async def test_add_con_arroba_normaliza(maker):
    """add: username con @ se normaliza (lstrip)."""
    async with maker() as s:
        await accounts.add(s, "@conarroba")
    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        assert row.username == "conarroba"


async def test_add_then_monitor_no_arranca_monitor_global(maker):
    """T60: --then-monitor solo setea la bandera; no arranca el monitor global."""
    async with maker() as s:
        await accounts.add(s, "usuario", then_monitor=True)
    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        assert row.monitor_after_backfill is True
        assert row.mode == "history"  # sigue en history; no 'monitor'


async def test_pause_resume(maker):
    """pause/resume: par simétrico."""
    async with maker() as s:
        await accounts.add(s, "usuario")
        await accounts.pause(s, "usuario")
    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        assert row.paused is True
    async with maker() as s:
        await accounts.resume(s, "usuario")
    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        assert row.paused is False


async def test_set_notify(maker):
    """L-G3: notify on/off activa notify_on_download."""
    async with maker() as s:
        await accounts.add(s, "usuario")
        await accounts.set_notify(s, "usuario", True)
    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        assert row.notify_on_download is True

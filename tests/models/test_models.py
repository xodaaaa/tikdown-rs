"""e01s04 — Modelos SQLAlchemy async (§2)."""

# story: e01s04
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.models.models import Base, DaemonState, MonitoredAccount


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_monitored_account_creacion(session):
    """Crear una cuenta con defaults; mode history por defecto."""
    acct = MonitoredAccount(username="usuario")
    session.add(acct)
    await session.commit()
    result = await session.execute(select(MonitoredAccount))
    row = result.scalar_one()
    assert row.username == "usuario"
    assert row.mode == "history"
    assert row.paused is False
    assert row.backfill_status == "idle"


async def test_monitored_account_username_unico(session):
    """username UNIQUE — segundo insert con mismo username viola integridad."""
    session.add(MonitoredAccount(username="dup"))
    await session.commit()
    session.add(MonitoredAccount(username="dup"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_daemon_state_singleton_check(session):
    """DaemonState es singleton: id=1 forzado por CHECK (id = 1)."""
    ds = DaemonState(id=1)
    session.add(ds)
    await session.commit()
    result = await session.execute(select(DaemonState))
    row = result.scalar_one()
    assert row.id == 1
    assert row.monitor_running is False

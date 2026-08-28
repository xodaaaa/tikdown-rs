"""e07s04 — contención SQLite: persistencia (T19/T37), alerta con dedupe (flanco)."""
# story: e07s04
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.core.db import busy_count, record_busy
from tikdown_rs.models.models import Base, DaemonState


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_record_busy_incrementa():
    """El listener captura y record_busy incrementa (ventana rotativa)."""
    from tikdown_rs.core.db import _busy_timestamps

    _busy_timestamps.clear()
    before = busy_count()
    record_busy()
    assert busy_count() > before


async def test_persist_busy_count_t37_t19(maker):
    """T37/T19: persist_busy_count persiste en daemon_state (commit interno)."""
    from tikdown_rs.core.daemon_state import persist_busy_count

    async with maker() as s:
        await persist_busy_count(s, count=7)
    async with maker() as s:
        row = (await s.execute(select(DaemonState))).scalar_one()
        assert row.db_busy_count_5min == 7


def test_alerta_dedupe_por_flanco():
    """§5.8: la alerta se emite al CRUZAR el umbral, no en cada chequeo."""
    from tikdown_rs.core.db import ContentionAlerter

    alerter = ContentionAlerter(threshold=20)
    events = []

    def _on_event(event, payload):
        events.append(event)

    # Bajo umbral → no alerta
    assert alerter.check(5, on_event=_on_event) is False
    # Cruza umbral → alerta (flanco ascendente)
    assert alerter.check(25, on_event=_on_event) is True
    assert events.count("daemon.db_contention") == 1
    # Sigue alto → NO repite (dedupe)
    assert alerter.check(30, on_event=_on_event) is False
    assert events.count("daemon.db_contention") == 1
    # Baja y vuelve a cruzar → re-alerta (nuevo flanco)
    alerter.check(5, on_event=_on_event)  # baja
    assert alerter.check(25, on_event=_on_event) is True
    assert events.count("daemon.db_contention") == 2

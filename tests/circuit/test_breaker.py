"""e07s03 — circuit breaker por cuenta (§4.4): auth → paused+needs_review."""
# story: e07s03
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.core.breaker import AccountBreaker
from tikdown_rs.models.models import Base, MonitoredAccount
from tikdown_rs.services import accounts


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_5_auth_pausa_cuenta(maker):
    """§4.4: 5 fallos de auth consecutivos → paused + needs_review."""
    async with maker() as s:
        await accounts.add(s, "u1")

    breaker = AccountBreaker(threshold=5)
    events = []

    def _on_event(event, payload):
        events.append(event)

    async with maker() as s:
        for _ in range(4):
            tripped = await breaker.record_result(s, "u1", "auth", on_event=_on_event)
            assert tripped is False
        tripped = await breaker.record_result(s, "u1", "auth", on_event=_on_event)
        assert tripped is True  # 5º → dispara

    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        assert row.paused is True
        assert row.needs_review is True
    assert "monitor.account_paused" in events


async def test_transitorio_no_cuenta(maker):
    """§4.4/T5: fallos transitorios NO cuentan para el breaker."""
    async with maker() as s:
        await accounts.add(s, "u1")

    breaker = AccountBreaker(threshold=5)
    async with maker() as s:
        for _ in range(10):
            tripped = await breaker.record_result(s, "u1", "transient")
            assert tripped is False  # nunca dispara con transitorios

    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        assert row.paused is False


async def test_exito_resetea(maker):
    """§4.4: un éxito resetea el contador."""
    async with maker() as s:
        await accounts.add(s, "u1")

    breaker = AccountBreaker(threshold=3)
    async with maker() as s:
        await breaker.record_result(s, "u1", "auth")
        await breaker.record_result(s, "u1", "auth")
        await breaker.record_result(s, "u1", "success")  # reset
        tripped = await breaker.record_result(s, "u1", "auth")
        assert tripped is False  # solo 1 tras el reset


async def test_red_disco_no_cuentan_t45_t64(maker):
    """T45/T64: red/disco no cuentan para el breaker."""
    async with maker() as s:
        await accounts.add(s, "u1")

    breaker = AccountBreaker(threshold=5)
    async with maker() as s:
        for _ in range(10):
            await breaker.record_result(s, "u1", "network")
            await breaker.record_result(s, "u1", "disk")
    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        assert row.paused is False


def test_contador_en_memoria_reset():
    """§4.4: nueva instancia = contador reseteado (en memoria del proceso)."""
    b1 = AccountBreaker(threshold=5)
    b1._counts["u1"] = 4
    b2 = AccountBreaker(threshold=5)  # reinicio → reset
    assert b2.count_for("u1") == 0

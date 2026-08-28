"""e04s01 — cooldown global cross-proceso: T22, T62, L-C6, L-C7."""

# story: e04s01
import random

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.core.pacing import CooldownReserve, reserve_slot
from tikdown_rs.models.models import Base, DownloadPacingState


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_reserve_slot_sorteo_dentro_rango(maker):
    """T62: el sorteo cae dentro de [MIN, MAX] (RNG inyectable)."""
    reserve = CooldownReserve(min_seconds=10, max_seconds=20, rng=random.Random(42))
    async with maker() as s:
        delay = await reserve_slot(s, reserve)
    assert 10 <= delay <= 20


async def test_reserve_min_igual_max_fijo(maker):
    """T62: MIN=MAX → sorteo fijo."""
    reserve = CooldownReserve(min_seconds=5, max_seconds=5, rng=random.Random(1))
    async with maker() as s:
        delay = await reserve_slot(s, reserve)
    assert delay == 5


async def test_reserve_cero_desactivado(maker):
    """T62: ambos 0 → desactivado (delay 0)."""
    reserve = CooldownReserve(min_seconds=0, max_seconds=0, rng=random.Random(1))
    async with maker() as s:
        delay = await reserve_slot(s, reserve)
    assert delay == 0


async def test_reserve_cross_proceso_persistido(maker):
    """T22/L-C7: next_allowed_at persistido con milisegundos (CAS visible entre procesos)."""
    reserve = CooldownReserve(min_seconds=30, max_seconds=60, rng=random.Random(7))
    async with maker() as s:
        await reserve_slot(s, reserve)
    from sqlalchemy import select

    async with maker() as s:
        row = (await s.execute(select(DownloadPacingState))).scalar_one()
        assert row.next_allowed_at is not None
        # L-C7: precisión de milisegundos (debe contener '.' de los ms)
        assert "." in row.next_allowed_at

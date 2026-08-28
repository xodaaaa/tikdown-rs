"""e13s01 — backfills pausados: estado 'paused', recogida, slot cross-proceso (T22).

Cubre: productor de 'paused' (causa red/disco), 'queued' en crash (F-10),
slot cross-proceso CAS (T22), collect de paused reanudables, reconcile no toca paused.

story: e13s01
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.models.models import Base, MonitoredAccount


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _add_account(session, username="user1") -> MonitoredAccount:
    acc = MonitoredAccount(username=username, mode="history")
    session.add(acc)
    await session.commit()
    return acc


# --- Productor de 'paused' ---


async def test_cancel_con_disco_paused_marca_paused(session):
    """Interrupción con disco pausado → backfill_status='paused'."""
    from tikdown_rs.core.disk import set_downloads_paused
    from tikdown_rs.services import backfill

    # Simular disco pausado
    await set_downloads_paused(session, True)
    # El productor de paused se invoca desde el except CancelledError de run_backfill;
    # testeamos el helper que decide el estado
    status = backfill.status_after_interruption(session, paused_disk=True, network_online=False)
    assert status == "paused"


async def test_cancel_sin_causa_vuelve_queued(session):
    """Crash sin causa → 'queued' (F-10 conservado)."""
    from tikdown_rs.services import backfill

    status = backfill.status_after_interruption(session, paused_disk=False, network_online=True)
    assert status == "queued"


async def test_cancel_red_offline_marca_paused(session):
    """Red offline → 'paused'."""
    from tikdown_rs.services import backfill

    status = backfill.status_after_interruption(session, paused_disk=False, network_online=False)
    assert status == "paused"


# --- Slot cross-proceso (T22) ---


async def test_slot_cross_proceso_adquisicion_atomica(session):
    """Dos procesos simulados → solo uno gana el slot (CAS)."""
    from tikdown_rs.services import backfill

    # Primera adquisición (proceso A)
    acquired_a = await backfill.acquire_slot(session, owner="proc-A")
    assert acquired_a is True
    # Segunda adquisición (proceso B) — debe fallar
    acquired_b = await backfill.acquire_slot(session, owner="proc-B")
    assert acquired_b is False


async def test_slot_release_permite_nuevo_owner(session):
    """Liberar el slot permite que otro proceso lo adquiera."""
    from tikdown_rs.services import backfill

    assert await backfill.acquire_slot(session, owner="proc-A") is True
    await backfill.release_slot(session, owner="proc-A")
    assert await backfill.acquire_slot(session, owner="proc-B") is True


async def test_slot_busy_detecta_ocupado(session):
    """backfill_slot_busy refleja el estado cross-proceso."""
    from tikdown_rs.services import backfill

    assert await backfill.backfill_slot_busy(session) is False
    await backfill.acquire_slot(session, owner="proc-A")
    assert await backfill.backfill_slot_busy(session) is True


async def test_slot_no_liberable_por_otro_owner(session):
    """Solo el owner puede liberar el slot."""
    from tikdown_rs.services import backfill

    await backfill.acquire_slot(session, owner="proc-A")
    await backfill.release_slot(session, owner="proc-B")  # no-op
    assert await backfill.backfill_slot_busy(session) is True


# --- Recogida de paused reanudables (F-10) ---


async def test_collect_incluye_paused_reanudable(session):
    """collect_queued_backfills recoge 'paused' con causa resuelta (red+disco OK)."""
    from tikdown_rs.services import backfill

    acc = await _add_account(session, "user1")
    acc.backfill_status = "paused"
    await session.commit()

    outcomes = await backfill.collect_queued_backfills(
        session,
        engine=None,
        cookies=["c1"],
    )
    assert len(outcomes) == 1
    assert "user1" in outcomes[0]


async def test_collect_excluye_paused_con_causa_activa(session):
    """collect NO recoge 'paused' si disco sigue pausado o red offline."""
    from tikdown_rs.core.disk import set_downloads_paused
    from tikdown_rs.services import backfill

    acc = await _add_account(session, "user1")
    acc.backfill_status = "paused"
    await session.commit()
    await set_downloads_paused(session, True)

    outcomes = await backfill.collect_queued_backfills(
        session,
        engine=None,
        cookies=["c1"],
    )
    assert outcomes == []  # disco pausado → no reanudar


async def test_collect_incluye_queued(session):
    """'queued' sigue recogiéndose (F-10)."""
    from tikdown_rs.services import backfill

    acc = await _add_account(session, "user1")
    acc.backfill_status = "queued"
    await session.commit()

    outcomes = await backfill.collect_queued_backfills(
        session,
        engine=None,
        cookies=["c1"],
    )
    assert len(outcomes) == 1


async def test_reconcile_no_toca_paused(session):
    """reconcile_stale_backfills no toca 'paused' (solo 'backfilling')."""
    from tikdown_rs.services import backfill

    acc = await _add_account(session, "user1")
    acc.backfill_status = "paused"
    await session.commit()

    count = await backfill.reconcile_stale_backfills(session)
    assert count == 0
    # sigue paused
    result = await session.execute(
        select(MonitoredAccount).where(MonitoredAccount.username == "user1")
    )
    assert result.scalar_one().backfill_status == "paused"

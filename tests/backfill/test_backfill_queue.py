"""e04s03 — cola: slot único (F-10), propagación canal (T75), transición (T59)."""
# story: e04s03

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.models.models import Base, MonitoredAccount
from tikdown_rs.services import accounts
from tikdown_rs.services.backfill import (
    collect_queued_backfills,
    reconcile_transitions,
)


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_slot_adquisicion_no_bloqueante(maker):
    """F-10/§10/e13s01: slot cross-proceso — libre → adquiere; ocupado → False."""
    from tikdown_rs.services.backfill import acquire_slot, release_slot

    async with maker() as s:
        assert await acquire_slot(s, owner="proc-A") is True  # libre → adquirido
        assert await acquire_slot(s, owner="proc-B") is False  # ocupado → no bloquea
        await release_slot(s, owner="proc-A")


async def test_collect_queued_propaga_canal_t75(maker):
    """T75: collect_queued_backfills propaga on_event a run_backfill."""
    events = []

    def _on_event(event, payload):  # canal SÍNCRONO (L-G2)
        events.append(event)

    class _FakeEngine:
        async def download(self, url, archive_path=None, **kw):
            return {"status": "downloaded", "tiktok_video_id": "11111"}

        async def extract_profile(self, username):
            return {"entries": []}

    _fake_engine_download = _FakeEngine()

    async with maker() as s:
        await accounts.add(s, "u1", mode="history")
        from tikdown_rs.core.daemon_state import get_or_create_daemon_state  # noqa
    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        row.backfill_status = "queued"  # encolado
        await s.commit()

    async with maker() as s:
        await collect_queued_backfills(
            s, engine=_fake_engine_download, cookies=["c"], on_event=_on_event
        )
    # El canal propagado recibió el evento de completado (T75)
    assert "backfill.completed" in events


async def test_transicion_then_monitor_misma_transaccion_t59(maker):
    """T59: --then-monitor transiciona en la misma transacción que el completado."""
    async with maker() as s:
        await accounts.add(s, "u1", mode="history", then_monitor=True)
    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        row.backfill_status = "completed"  # backfill completado
        await s.commit()

    # Reconciliación de transiciones pendientes (arranque)
    async with maker() as s:
        await reconcile_transitions(s)
    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        assert row.mode == "monitor"  # transicionó
        assert row.monitor_after_backfill is False  # bandera consumida

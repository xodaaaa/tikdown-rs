"""e04s02 — backfill foreground: cursor §10, L-F1/L-F2, F-09, F-10, T21, F-01."""
# story: e04s02

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.models.models import Base, MonitoredAccount
from tikdown_rs.services import accounts
from tikdown_rs.services.backfill import (
    cursor_should_advance,
    reconcile_stale_backfills,
    run_backfill,
)


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_cursor_comparacion_estricta_menor():
    """§10: el cursor avanza solo si upload_date < cursor (nunca ==)."""
    # upload_date == cursor → no avanza (evita perder/repetir en el borde)
    assert cursor_should_advance(upload_date="20260101", cursor="20260101") is False
    # upload_date < cursor → avanza
    assert cursor_should_advance(upload_date="20251231", cursor="20260101") is True
    # upload_date > cursor → no (fuera de alcance)
    assert cursor_should_advance(upload_date="20260102", cursor="20260101") is False


def test_cursor_solo_estado_terminal():
    """§10: el cursor avanza solo en estado terminal (downloaded/failed/skipped)."""
    assert (
        cursor_should_advance(upload_date="20260101", cursor="20260102", status="downloaded")
        is True
    )
    assert cursor_should_advance(upload_date="20260101", cursor="20260102", status="failed") is True
    assert (
        cursor_should_advance(upload_date="20260101", cursor="20260102", status="skipped") is True
    )
    # cancelled NO es terminal para el cursor
    assert (
        cursor_should_advance(upload_date="20260101", cursor="20260102", status="cancelled")
        is False
    )


def test_upload_date_ausente_fallback_lf2():
    """L-F2: upload_date ausente → fallback al cursor anterior (no NULL)."""
    from tikdown_rs.services.backfill import effective_upload_date

    assert effective_upload_date(None, cursor="20251201") == "20251201"
    assert effective_upload_date("20260101", cursor="20251201") == "20260101"


async def test_backfill_total_persistido_f09(maker):
    """F-09: run_backfill persiste backfill_total al iniciar."""
    async with maker() as s:
        await accounts.add(s, "usuario", mode="history")

    async def _fake_engine_download(url, archive_path=None, **kw):
        return {"status": "downloaded", "tiktok_video_id": "11111"}

    async with maker() as s:
        await run_backfill(
            s, "usuario", engine=_fake_engine_download, cookies=["cookie"], feed_entries=[]
        )
    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        assert row.backfill_status in ("completed", "backfilling")
        assert row.backfill_total == 0  # sin entradas de feed


async def test_no_cookies_aborta_f01(maker):
    """F-01: sin cookies → backfill aborta con backfill.no_cookies."""
    async with maker() as s:
        await accounts.add(s, "usuario", mode="history")

    from tikdown_rs.services.backfill import NoCookiesError

    async with maker() as s:
        with pytest.raises(NoCookiesError):
            await run_backfill(s, "usuario", engine=None, cookies=[], feed_entries=[])


async def test_reconcile_stale_backfills_f10(maker):
    """F-10: backfills huérfanos en backfilling → vuelven a queued."""
    async with maker() as s:
        await accounts.add(s, "usuario", mode="history")
        from tikdown_rs.core.daemon_state import get_or_create_daemon_state  # noqa
    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        row.backfill_status = "backfilling"
        await s.commit()
    async with maker() as s:
        await reconcile_stale_backfills(s)
    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        assert row.backfill_status == "queued"

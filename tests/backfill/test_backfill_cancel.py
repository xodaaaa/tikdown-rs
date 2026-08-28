"""e04s03 — cancelación cooperativa (T21) + retry-failed (T58/T63/T64)."""
# story: e04s03
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.models.models import Base, MonitoredAccount, Video
from tikdown_rs.services import accounts
from tikdown_rs.services.backfill import cancel_backfill
from tikdown_rs.services.videos import retry_exhausted, should_retry


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_cancel_backfill_marca_cancelled(maker):
    """T21: cancel_backfill marca 'cancelled'."""
    async with maker() as s:
        await accounts.add(s, "u1")
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        row.backfill_status = "backfilling"
        await s.commit()
    async with maker() as s:
        await cancel_backfill(s, "u1")
    async with maker() as s:
        row = (await s.execute(select(MonitoredAccount))).scalar_one()
        assert row.backfill_status == "cancelled"


def test_retry_exhausted_t58():
    """T58: techo de reintentos → failed/transient + retry_exhausted."""
    video = Video(
        tiktok_video_id="1", retry_count=5,  # == MAX_VIDEO_RETRY_COUNT
        status="downloaded", error_category="transient",
    )
    assert retry_exhausted(video, max_retry=5) is True
    video2 = Video(tiktok_video_id="2", retry_count=2, status="downloaded")
    assert retry_exhausted(video2, max_retry=5) is False


def test_should_retry_no_penaliza_red_t64():
    """T64: un fallo de red no consume reintentos."""
    # Un fallo de red (network offline) → no incrementa retry_count
    assert should_retry(error_category="network", retry_count=4, max_retry=5) is True
    # Un fallo transitorio normal sí consume
    assert should_retry(error_category="transient", retry_count=4, max_retry=5) is True
    # Techo alcanzado (transitorio) → no más reintentos
    assert should_retry(error_category="transient", retry_count=5, max_retry=5) is False

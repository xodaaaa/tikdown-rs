"""e07s02 — disco: ENOSPC (T45), job (T65), reanudación, --resume (T69 mock)."""

# story: e07s02
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.core.config import Settings
from tikdown_rs.core.disk import check_disk_usage, disk_job, set_downloads_paused
from tikdown_rs.models.models import Base, DaemonState


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _disk(free_percent: float):
    """Fake shutil.disk_usage con % de espacio libre controlado (T69)."""
    total = 100.0
    free = total * free_percent / 100
    return (total, total - free, free)


async def test_enospc_pone_downloads_paused_t45(maker, monkeypatch):
    """T45: disco casi lleno → downloads_paused=True."""
    from tikdown_rs.core import disk as disk_mod

    settings = Settings(_env_file=None, disk_warning_free_percent=10)
    monkeypatch.setattr(disk_mod.shutil, "disk_usage", lambda _p: _disk(1.0))  # 1% libre

    async with maker() as s:
        await check_disk_usage(s, settings)
    async with maker() as s:
        row = (await s.execute(select(DaemonState))).scalar_one()
        assert row.downloads_paused is True


async def test_espacio_libre_reanuda_t65(maker, monkeypatch):
    """T65: espacio libre por encima del umbral → reanudación automática."""
    from tikdown_rs.core import disk as disk_mod

    settings = Settings(_env_file=None, disk_warning_free_percent=10)
    # Primero: casi lleno → paused
    monkeypatch.setattr(disk_mod.shutil, "disk_usage", lambda _p: _disk(1.0))
    async with maker() as s:
        await check_disk_usage(s, settings)
    # Luego: espacio libre → reanuda
    monkeypatch.setattr(disk_mod.shutil, "disk_usage", lambda _p: _disk(50.0))
    async with maker() as s:
        await check_disk_usage(s, settings)
    async with maker() as s:
        row = (await s.execute(select(DaemonState))).scalar_one()
        assert row.downloads_paused is False


async def test_job_disk_warning_t65(maker, monkeypatch):
    """T65: bajo umbral → el job emite monitor.disk_warning."""
    from tikdown_rs.core import disk as disk_mod

    settings = Settings(_env_file=None, disk_warning_free_percent=10)
    monkeypatch.setattr(disk_mod.shutil, "disk_usage", lambda _p: _disk(5.0))
    events = []

    def _on_event(event, payload):
        events.append(event)

    async with maker() as s:
        await disk_job(s, settings, on_event=_on_event)
    assert "monitor.disk_warning" in events


async def test_resume_manual_limpia_flag(maker):
    """§3: set_downloads_paused(False) limpia el flag (system disk --resume)."""
    async with maker() as s:
        await set_downloads_paused(s, True)
    async with maker() as s:
        await set_downloads_paused(s, False)
    async with maker() as s:
        row = (await s.execute(select(DaemonState))).scalar_one()
        assert row.downloads_paused is False

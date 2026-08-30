"""e02s03 — runner: arranque, helpers commit interno (T37), monitor detenido (T5.1)."""

# story: e02s03
import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.core.daemon_state import set_monitor_running, set_stop_requested
from tikdown_rs.models.models import Base, DaemonState


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_set_stop_requested_commitea_interno(maker):
    """T37: el helper mutador commitea internamente (no depende del llamador)."""
    async with maker() as s:
        await set_stop_requested(s, True)
    # Nueva sesión: el cambio PERSISTE (commit interno)
    async with maker() as s:
        row = (await s.execute(select(DaemonState))).scalar_one()
        assert row.stop_requested is True


async def test_set_monitor_running_commitea_interno(maker):
    """T37: set_monitor_running commitea internamente."""
    async with maker() as s:
        await set_monitor_running(s, True)
    async with maker() as s:
        row = (await s.execute(select(DaemonState))).scalar_one()
        assert row.monitor_running is True


async def test_monitor_autostart_false_default():
    """T5.1: el monitor arranca detenido por defecto (Settings default)."""
    from tikdown_rs.core.config import Settings

    s = Settings(_env_file=None)
    assert s.monitor_autostart is False


# --- Bug #21 (F-03): el daemon debe detenerse al leer stop_requested en DB ---


async def test_check_stop_requested_activa_stop_event():
    """Bug #21: stop_requested=True en DB activa el stop event del runner."""
    from tikdown_rs.core.config import Settings
    from tikdown_rs.daemon.run import DaemonRunner

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        runner = DaemonRunner(Settings(_env_file=None))
        runner._engine = engine
        async with maker() as s:
            await set_stop_requested(s, True)
        assert not runner._stop_event.is_set()
        await runner._check_stop_requested()
        assert runner._stop_event.is_set()
    finally:
        await engine.dispose()


async def test_run_se_detiene_y_consumira_stop_requested():
    """Bug #21: _run() termina cuando daemon stop escribe stop_requested,
    y resetea el flag para que el proximo arranque no se apague en seco."""
    from tikdown_rs.core.config import Settings
    from tikdown_rs.daemon.run import DaemonRunner

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    run_task = None
    try:
        # heartbeat_interval_seconds=1 -> sondeo de stop cada ~0.5s
        runner = DaemonRunner(Settings(_env_file=None, heartbeat_interval_seconds=1))
        runner._engine = engine
        run_task = asyncio.create_task(runner._run())
        await asyncio.sleep(0.1)
        async with maker() as s:
            await set_stop_requested(s, True)
        await asyncio.wait_for(run_task, timeout=5.0)
        assert runner._stop_event.is_set()
        # Flag consumido: el proximo arranque no debe auto-apagarse
        async with maker() as s:
            row = (await s.execute(select(DaemonState))).scalar_one()
            assert row.stop_requested is False
    finally:
        if run_task is not None and not run_task.done():
            run_task.cancel()
        await engine.dispose()

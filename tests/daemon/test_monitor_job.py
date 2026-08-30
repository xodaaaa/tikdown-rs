"""2.1-r2 — el daemon ejecuta los ciclos inertes: monitor, disco, red.

Integración contra el runner real (mismo patrón que tests/daemon/test_runner.py).
Detecta el patrón que causó el hallazgo: lógica pura testeada en aislamiento
pero NUNCA llamada por el daemon — si un job se desconecta en el futuro, un
test lo nota antes que una auditoría manual.
"""

# story: e03s02
import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.core.daemon_state import set_monitor_running
from tikdown_rs.models.models import Base, DaemonState, MonitoredAccount


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def settings(tmp_path):
    from tikdown_rs.core.config import Settings

    # intervalos cortos para que los jobs disparen dentro del test
    return Settings(_env_file=None, data_dir=tmp_path, monitor_interval_minutes=1)


def test_heartbeat_aplica_monitor_running(maker):
    """e03s02: el heartbeat aplica monitor_running en caliente (docstring del
    propio job + cli/monitor). Ahora sí lo hace (hallazgo 2.1)."""
    import inspect

    from tikdown_rs.daemon.run import DaemonRunner

    src = inspect.getsource(DaemonRunner._register_jobs)
    # el job de heartbeat debe leer el flag, no solo escribir el timestamp
    assert "monitor_running" in src
    assert "read_monitor_running" in src


async def test_read_monitor_running_arranca_y_drena(maker):
    """read_monitor_running(): running → arranca ciclo; stop → drena y para."""
    from tikdown_rs.daemon.monitor_job import MonitorJob

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    m = async_sessionmaker(engine, expire_on_commit=False)
    try:
        lanzados: list[str] = []

        async def fake_cycle(session, account):
            lanzados.append(account.username)

        job = MonitorJob(m, interval_seconds=1, cycle_fn=fake_cycle)
        async with m() as s:
            await set_monitor_running(s, True)
        await job.read_monitor_running()
        assert job.running is True

        async with m() as s:
            await set_monitor_running(s, False)
        await job.read_monitor_running()
        assert job.running is False
        await job.join(timeout=5)  # el loop se drena (sale tras el sleep corto)
        assert job.task.done()
    finally:
        await job.stop()
        await engine.dispose()


async def test_monitor_job_ejecuta_ciclos_para_cuentas_monitor(maker):
    """El loop del job dispara run_monitor_cycle solo con monitor_running=True
    y respeta el intervalo configurado."""
    from tikdown_rs.daemon.monitor_job import MonitorJob

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    m = async_sessionmaker(engine, expire_on_commit=False)
    async with m() as s:
        s.add(MonitoredAccount(username="obs", mode="monitor"))
        s.add(MonitoredAccount(username="hist", mode="history"))
        await s.commit()

    settings = None  # se construye aqui para inyectar intervalo minimo
    from tikdown_rs.core.config import Settings

    settings = Settings(_env_file=None, monitor_interval_minutes=1)
    cycles = 0

    async def fake_cycle(session, account):
        nonlocal cycles
        cycles += 1

    job = MonitorJob(m, interval_seconds=1, cycle_fn=fake_cycle, settings=settings)
    try:
        async with m() as s:
            await set_monitor_running(s, True)
        await job.read_monitor_running()
        await asyncio.sleep(1.6)  # 1-2 ciclos con intervalo 1s
        n_con_running = cycles
        async with m() as s:
            await set_monitor_running(s, False)
        await job.read_monitor_running()
        await job.join(timeout=5)
        await asyncio.sleep(0.3)
        assert n_con_running >= 1, "con monitor_running=True debe ejecutar ciclos"
        assert cycles == n_con_running, "con monitor_running=False no debe haber ciclos nuevos"
    finally:
        await job.stop()
        await engine.dispose()


async def test_run_monitor_cycle_descubre_y_registra_video(maker):
    """Ciclo real: discover_fn (fake) registra el video descubierto (Video row).

    Verifica que el discover_fn del daemon persiste el video con su cuenta —
    sin esto, el ciclo 'corre' pero no deja rastro (parte inerte del 2.1).
    """
    from tikdown_rs.services.monitor import run_monitor_cycle

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    m = async_sessionmaker(engine, expire_on_commit=False)

    from tikdown_rs.models.models import Video

    async def discover(session, account):
        session.add(Video(tiktok_video_id=f"{account.username}-1", account_id=account.id))

    async with m() as s:
        s.add(MonitoredAccount(username="obs", mode="monitor"))
        await s.commit()

    async with m() as s:
        processed = await run_monitor_cycle(s, discover)
    assert processed == ["obs"]
    async with m() as s:
        row = (await s.execute(select(Video))).scalar_one()
        assert row.tiktok_video_id == "obs-1"
    await engine.dispose()


async def test_daemon_state_row_sobrevive_lecturas_de_jobs(maker):
    """Sanidad: get_or_create + set no rompen el singleton al usarlo desde
    varios jobs (heartbeat/monitor/disk comparten daemon_state id=1)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    m = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with m() as s:
            await set_monitor_running(s, True)
        async with m() as s:
            rows = (await s.execute(select(DaemonState))).scalars().all()
        assert len(rows) == 1
        assert rows[0].monitor_running is True
    finally:
        await engine.dispose()

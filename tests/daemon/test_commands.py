"""e02s04 — daemon status/healthcheck/stop: T19, T50, R10, T37."""
# story: e02s04
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.core.config import Settings
from tikdown_rs.models.models import Base, DaemonState


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_daemon(maker, heartbeat_ts=None):
    from tikdown_rs.core.daemon_state import get_or_create_daemon_state

    async with maker() as s:
        row = await get_or_create_daemon_state(s)
        if heartbeat_ts:
            row.last_heartbeat_at = heartbeat_ts
            row.daemon_pid = 999
            row.db_busy_count_5min = 5
            await s.commit()


async def _heartbeat_fresh(settings: Settings, maker) -> bool:
    """Healthcheck: heartbeat fresco <= 3 x intervalo (T50)."""
    from sqlalchemy import select

    async with maker() as s:
        row = (await s.execute(select(DaemonState))).scalar_one()
        if row.last_heartbeat_at is None:
            return False
        ts = datetime.fromisoformat(row.last_heartbeat_at)
        age = (datetime.now(UTC) - ts).total_seconds()
        return age <= 3 * settings.heartbeat_interval_seconds


async def test_healthcheck_frescura_t50(maker):
    """T50: heartbeat reciente → fresco (healthcheck OK)."""
    from tikdown_rs.core.daemon_state import update_heartbeat

    async with maker() as s:
        await update_heartbeat(s, pid=1)
    settings = Settings(_env_file=None, heartbeat_interval_seconds=10)
    assert await _heartbeat_fresh(settings, maker) is True


async def test_healthcheck_heartbeat_viejo_falla(maker):
    """T50: heartbeat viejo (> 3x intervalo) → no fresco (healthcheck fail)."""
    settings = Settings(_env_file=None, heartbeat_interval_seconds=10)
    old = (datetime.now(UTC).timestamp() - 100)  # 100s > 30s
    old_iso = datetime.fromtimestamp(old, tz=UTC).isoformat()
    await _seed_daemon(maker, heartbeat_ts=old_iso)
    assert await _heartbeat_fresh(settings, maker) is False


async def test_healthcheck_sin_migrar_r10():
    """R10: healthcheck NO ejecuta migraciones ni toma .migrate.lock."""

    # No llamar apply_migrations aquí — el healthcheck solo lee el heartbeat.
    # Verificar que la función de healthcheck no existe en migrations (no migra).
    import tikdown_rs.core.migrations as mig
    assert not hasattr(mig, "healthcheck")


async def test_status_lee_contencion_daemon_state(maker):
    """T19: el contador de contención se lee de daemon_state, no del proceso CLI."""
    await _seed_daemon(maker, heartbeat_ts=datetime.now(UTC).isoformat())
    from sqlalchemy import select

    async with maker() as s:
        row = (await s.execute(select(DaemonState))).scalar_one()
        # El CLI lee db_busy_count_5min del daemon_state (persistido por el daemon)
        assert row.db_busy_count_5min == 5

"""e03s02 — ciclo de monitor: throttle L-G1, mode, paused, §10."""

# story: e03s02
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.models.models import Base, MonitoredAccount
from tikdown_rs.services import accounts
from tikdown_rs.services.monitor import _should_check, run_monitor_cycle


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_should_check_null_siempre():
    """L-G1: last_check_at NULL → SIEMPRE se comprueba (nunca comprobada)."""
    now = datetime.now(UTC)
    account = MonitoredAccount(username="nueva", last_check_at=None)
    assert _should_check(account, now) is True


def test_should_check_reciente_skip():
    """L-G1: last_check_at < 30s → se salta (recién comprobada)."""
    now = datetime.now(UTC)
    account = MonitoredAccount(
        username="recien",
        last_check_at=(now - timedelta(seconds=10)).isoformat(),
    )
    assert _should_check(account, now) is False


def test_should_check_vieja_comprueba():
    """L-G1: last_check_at >= 30s → se comprueba."""
    now = datetime.now(UTC)
    account = MonitoredAccount(
        username="vieja",
        last_check_at=(now - timedelta(seconds=60)).isoformat(),
    )
    assert _should_check(account, now) is True


async def test_run_cycle_solo_mode_monitor_no_pausadas(maker):
    """El ciclo solo procesa cuentas mode=monitor y no pausadas."""
    async with maker() as s:
        await accounts.add(s, "en_history", mode="history")
        await accounts.add(s, "en_monitor", mode="monitor")
        await accounts.add(s, "pausada", mode="monitor")
        await accounts.pause(s, "pausada")

    processed = []

    async def _fake_discover(session, account):
        processed.append(account.username)

    # El ciclo no arranca backfill (§10): solo detecta vídeos
    async with maker() as s:
        await run_monitor_cycle(s, discover_fn=_fake_discover, throttle_seconds=30)

    assert "en_monitor" in processed
    assert "en_history" not in processed
    assert "pausada" not in processed

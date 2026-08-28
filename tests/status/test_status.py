"""e15s01 — métricas y healthcheck: cookies, disco, errores, contención (T19/T50).

Cubre: collect_status (cookies válidas/expirando, disco, últimos errores,
contención), daemon status ampliado, healthcheck con cookies/disco/errores.

story: e15s01
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.core.config import Settings
from tikdown_rs.models.models import Base, Cookie, DaemonState, MonitoredAccount, Video


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _add_cookie(session, state="valid", expires_in_days=None) -> Cookie:
    c = Cookie(validation_state=state, encrypted_blob=b"blob")
    if expires_in_days is not None:
        c.expiration_date = (datetime.now(UTC) + timedelta(days=expires_in_days)).isoformat()
    session.add(c)
    await session.commit()
    return c


async def _add_video_failed(session, account_name="u1", category="definitive") -> Video:
    acc = MonitoredAccount(username=account_name, mode="history")
    session.add(acc)
    await session.commit()
    v = Video(
        tiktok_video_id=f"id_{account_name}_{category}",
        account_id=acc.id,
        status="failed",
        error_category=category,
        error_message="boom",
    )
    session.add(v)
    await session.commit()
    return v


# --- collect_status ---


async def test_collect_status_cookies(session):
    """collect_status cuenta cookies válidas/inválidas/expirando."""
    from tikdown_rs.services.status import collect_status

    await _add_cookie(session, "valid")
    await _add_cookie(session, "valid", expires_in_days=2)  # expirando
    await _add_cookie(session, "invalid")
    settings = Settings(_env_file=None)
    status = await collect_status(session, settings)
    assert status["cookies"]["valid"] >= 1
    assert status["cookies"]["invalid"] >= 1
    assert status["cookies"]["expiring"] >= 1


async def test_collect_status_disco(session, monkeypatch):
    """collect_status incluye disco (free_percent) + umbral."""
    from tikdown_rs.services.status import collect_status

    settings = Settings(_env_file=None, disk_warning_free_percent=10)
    monkeypatch.setattr(
        "tikdown_rs.core.disk.shutil.disk_usage",
        lambda _p: (100, 50, 25),  # 25% libre
    )
    status = await collect_status(session, settings)
    assert status["disk"]["free_percent"] == 25.0
    assert status["disk"]["warning_threshold"] == 10
    assert status["disk"]["alert"] is False


async def test_collect_status_disco_alerta(session, monkeypatch):
    """Disco bajo umbral → alert=True."""
    from tikdown_rs.services.status import collect_status

    settings = Settings(_env_file=None, disk_warning_free_percent=30)
    monkeypatch.setattr(
        "tikdown_rs.core.disk.shutil.disk_usage",
        lambda _p: (100, 50, 10),  # 10% libre
    )
    status = await collect_status(session, settings)
    assert status["disk"]["free_percent"] == 10.0
    assert status["disk"]["alert"] is True


async def test_collect_status_ultimos_errores(session):
    """collect_status incluye últimos errores (videos failed) con cuenta."""
    from tikdown_rs.services.status import collect_status

    await _add_video_failed(session, "u1", "definitive")
    settings = Settings(_env_file=None)
    status = await collect_status(session, settings)
    errors = status["recent_errors"]
    assert len(errors) >= 1
    assert errors[0]["account"] == "u1"
    assert errors[0]["category"] == "definitive"
    assert "timestamp" in errors[0]


async def test_collect_status_contencion_t19(session):
    """Contención leída de daemon_state (T19), no del proceso CLI."""
    from tikdown_rs.services.status import collect_status

    session.add(DaemonState(id=1, db_busy_count_5min=7))
    await session.commit()
    settings = Settings(_env_file=None)
    status = await collect_status(session, settings)
    assert status["contention"]["db_busy_count_5min"] == 7


# --- healthcheck ---


async def test_healthcheck_ok_con_cookie_y_disco(session, monkeypatch):
    """Heartbeat fresco + cookie válida + disco OK → True."""
    from tikdown_rs.services.status import healthcheck_status

    session.add(
        DaemonState(
            id=1,
            last_heartbeat_at=datetime.now(UTC).isoformat(),
            db_busy_count_5min=0,
        )
    )
    await _add_cookie(session, "valid")
    await session.commit()
    settings = Settings(_env_file=None, heartbeat_interval_seconds=10)
    monkeypatch.setattr(
        "tikdown_rs.core.disk.shutil.disk_usage",
        lambda _p: (100, 50, 40),  # 40% libre
    )
    ok, reasons = await healthcheck_status(session, settings)
    assert ok is True
    assert reasons == []


async def test_healthcheck_sin_cookie_falla(session, monkeypatch):
    """Sin cookie válida → False (razón 'no valid cookies')."""
    from tikdown_rs.services.status import healthcheck_status

    session.add(
        DaemonState(
            id=1,
            last_heartbeat_at=datetime.now(UTC).isoformat(),
            db_busy_count_5min=0,
        )
    )
    await session.commit()
    settings = Settings(_env_file=None, heartbeat_interval_seconds=10)
    monkeypatch.setattr("tikdown_rs.core.disk.shutil.disk_usage", lambda _p: (100, 50, 40))
    ok, reasons = await healthcheck_status(session, settings)
    assert ok is False
    assert any("cookie" in r for r in reasons)


async def test_healthcheck_disco_bajo_falla(session, monkeypatch):
    """Disco bajo umbral → False (razón 'disk')."""
    from tikdown_rs.services.status import healthcheck_status

    session.add(
        DaemonState(
            id=1,
            last_heartbeat_at=datetime.now(UTC).isoformat(),
            db_busy_count_5min=0,
        )
    )
    await _add_cookie(session, "valid")
    await session.commit()
    settings = Settings(_env_file=None, heartbeat_interval_seconds=10, disk_warning_free_percent=50)
    monkeypatch.setattr(
        "tikdown_rs.core.disk.shutil.disk_usage",
        lambda _p: (100, 50, 10),  # 10% < 50%
    )
    ok, reasons = await healthcheck_status(session, settings)
    assert ok is False
    assert any("disk" in r for r in reasons)


async def test_healthcheck_heartbeat_viejo_falla(session, monkeypatch):
    """Heartbeat viejo (> 3x intervalo) → False (T50)."""
    from tikdown_rs.services.status import healthcheck_status

    session.add(
        DaemonState(
            id=1,
            last_heartbeat_at=(datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
            db_busy_count_5min=0,
        )
    )
    await _add_cookie(session, "valid")
    await session.commit()
    settings = Settings(_env_file=None, heartbeat_interval_seconds=10)
    monkeypatch.setattr("tikdown_rs.core.disk.shutil.disk_usage", lambda _p: (100, 50, 40))
    ok, reasons = await healthcheck_status(session, settings)
    assert ok is False
    assert any("heartbeat" in r for r in reasons)

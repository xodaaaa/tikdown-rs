"""3.3-A — dispatch(): paridad funcional de los 12 comandos del bot (§6.4).

Cada comando se prueba contra services/* reales sobre DB in-memory
(exclusivamente mocks de red, §14 — aquí ni siquiera hay red).
"""

# story: e06s02
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.daemon.telegram.handlers import dispatch
from tikdown_rs.models.models import Base, Cookie, DaemonState, MonitoredAccount, Video


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

    return Settings(_env_file=None, data_dir=tmp_path)


async def _add_account(maker, **values) -> MonitoredAccount:
    async with maker() as s:
        acct = MonitoredAccount(username=values.pop("username", "usuario"), **values)
        s.add(acct)
        await s.commit()
        return acct


async def test_comando_desconocido(maker, settings):
    async with maker() as s:
        assert await dispatch("/noexiste", "", s, settings) == "Comando desconocido: /noexiste"


async def test_list_vacio_y_con_cuentas(maker, settings):
    async with maker() as s:
        assert await dispatch("/list", "", s, settings) == "No hay cuentas"
    await _add_account(maker, username="pepe")
    async with maker() as s:
        out = await dispatch("/list", "", s, settings)
    assert "@pepe" in out and "history" in out


async def test_add_y_stats(maker, settings):
    async with maker() as s:
        out = await dispatch("/add", "@nueva monitor", s, settings)
    assert "OK" in out and "@nueva" in out
    async with maker() as s:
        row = (
            await s.execute(select(MonitoredAccount).where(MonitoredAccount.username == "nueva"))
        ).scalar_one()
        assert row.mode == "monitor"
    async with maker() as s:
        out = await dispatch("/stats", "@nueva", s, settings)
    assert "followers=" in out and "backfill=idle" in out


async def test_stats_y_mutaciones_sin_usuario(maker, settings):
    for cmd in ("/stats", "/check", "/pause", "/resume", "/notify", "/backfill"):
        async with maker() as s:
            out = await dispatch(cmd, "", s, settings)
        assert out.startswith("ERROR falta usuario"), cmd


async def test_pause_resume_notify_check(maker, settings):
    await _add_account(maker, username="pepe")
    async with maker() as s:
        await dispatch("/pause", "@pepe", s, settings)
    async with maker() as s:
        assert (await _get_acct(s, "pepe")).paused is True
    async with maker() as s:
        out = await dispatch("/resume", "@pepe", s, settings)
    assert "reactivada" in out
    async with maker() as s:
        await dispatch("/notify", "@pepe on", s, settings)
    async with maker() as s:
        assert (await _get_acct(s, "pepe")).notify_on_download is True
    async with maker() as s:
        out = await dispatch("/notify", "@pepe off", s, settings)
    assert "OFF" in out
    async with maker() as s:
        out = await dispatch("/check", "@pepe", s, settings)
    assert "OK check @pepe" in out
    async with maker() as s:
        assert (await _get_acct(s, "pepe")).last_check_at is not None


async def _get_acct(s, username: str) -> MonitoredAccount:
    return (
        await s.execute(select(MonitoredAccount).where(MonitoredAccount.username == username))
    ).scalar_one()


async def test_cuenta_inexistente_error_plano(maker, settings):
    async with maker() as s:
        out = await dispatch("/stats", "@fantasma", s, settings)
    assert out.startswith("ERROR") and "fantasma" in out


async def test_last(maker, settings):
    async with maker() as s:
        assert await dispatch("/last", "", s, settings) == "Sin vídeos descargados"
    async with maker() as s:
        s.add(Video(tiktok_video_id="7301", downloaded_at="2026-08-29T10:00:00"))
        await s.commit()
    async with maker() as s:
        out = await dispatch("/last", "", s, settings)
    assert "7301" in out and "2026-08-29" in out


async def test_cookies(maker, settings):
    async with maker() as s:
        assert await dispatch("/cookies", "", s, settings) == "Sin cookies"
    async with maker() as s:
        s.add(Cookie(encrypted_blob=b"x", label="sesion1", validation_state="valid"))
        await s.commit()
    async with maker() as s:
        out = await dispatch("/cookies", "", s, settings)
    assert "#1" in out and "sesion1" in out and "valid" in out


async def test_disk(maker, settings):
    async with maker() as s:
        out = await dispatch("/disk", "", s, settings)
    assert "disco:" in out and "cookies:" in out


async def test_monitor_on_off(maker, settings):
    async with maker() as s:
        out = await dispatch("/monitor", "on", s, settings)
    assert "ON" in out
    async with maker() as s:
        assert (await s.execute(select(DaemonState))).scalar_one().monitor_running is True
    async with maker() as s:
        out = await dispatch("/monitor", "off", s, settings)
    assert "OFF" in out
    async with maker() as s:
        assert (await s.execute(select(DaemonState))).scalar_one().monitor_running is False


async def test_backfill_encola_y_rechaza(maker, settings):
    await _add_account(maker, username="pepe", backfill_status="completed")
    async with maker() as s:
        out = await dispatch("/backfill", "@pepe", s, settings)
    assert "encolado" in out and "completed" in out
    async with maker() as s:
        assert (await _get_acct(s, "pepe")).backfill_status == "queued"
    # En curso → rechazado
    async with maker() as s:
        acct = await _get_acct(s, "pepe")
        acct.backfill_status = "backfilling"
        await s.commit()
    async with maker() as s:
        out = await dispatch("/backfill", "@pepe", s, settings)
    assert "rechazado" in out

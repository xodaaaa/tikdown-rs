"""e09s01 — cli/videos.py: integrity/last (§3)."""

# story: e09s01
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.cli.videos import app
from tikdown_rs.models.models import Base, MonitoredAccount, Video


def test_cli_grupo_videos_comandos():
    """§3: el grupo videos tiene integrity y last."""
    commands = {c.name for c in app.registered_commands}
    assert {"integrity", "last"}.issubset(commands)


def test_cli_videos_solo_orquesta():
    """Regla de oro §3: cli/videos no importa yt_dlp."""
    import inspect

    import tikdown_rs.cli.videos as mod

    src = inspect.getsource(mod)
    assert "yt_dlp" not in src


# --- Auditoría 3.5: integrity [username] debe filtrar por cuenta real ---


def test_integrity_filtro_por_username():
    """Auditoría 3.5: integrity [username] filtra por la cuenta pedida (join),
    no por account_id IS NOT NULL (que ignoraba el username y devolvía los
    vídeos de CUALQUIER cuenta con cuenta asignada)."""
    import asyncio

    from tikdown_rs.cli.videos import _integrity_stmt

    async def _go():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            a1 = MonitoredAccount(username="uno")
            a2 = MonitoredAccount(username="dos")
            s.add_all([a1, a2])
            await s.flush()
            s.add_all(
                [
                    Video(tiktok_video_id="v1", account_id=a1.id),
                    Video(tiktok_video_id="v2", account_id=None),
                    Video(tiktok_video_id="v3", account_id=a1.id),
                    Video(tiktok_video_id="v4", account_id=a2.id),  # OTRA cuenta
                ]
            )
            await s.commit()
        async with maker() as s:
            stmt = _integrity_stmt("uno")
            ids = sorted(v.tiktok_video_id for v in (await s.execute(stmt)).scalars().all())
            stmt_all = _integrity_stmt(None)
            all_ids = sorted(v.tiktok_video_id for v in (await s.execute(stmt_all)).scalars().all())
        await engine.dispose()
        return ids, all_ids

    ids, all_ids = asyncio.run(_go())
    # Con username 'uno': SOLO sus vídeos — ni v2 (sin cuenta) ni v4 (de 'dos').
    # El filtro roto (is_not(None)) habría devuelto v1, v3 Y v4.
    assert ids == ["v1", "v3"]
    # Sin username: todos.
    assert all_ids == ["v1", "v2", "v3", "v4"]

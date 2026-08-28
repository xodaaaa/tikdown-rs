"""Comandos CLI del grupo videos — cli/videos.py (§3).

integrity [username], last, export. Solo orquesta services (regla de oro §3).

story: e09s01
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from tikdown_rs.core.config import Settings

app = typer.Typer(name="videos")


def _db_url(settings: Settings) -> str:
    return f"sqlite+aiosqlite:///{settings.data_dir / 'tikdown-rs.db'}"


@app.command("integrity")
def integrity(username: str | None = typer.Argument(None, help="Cuenta (opcional)")) -> None:
    """Verifica integridad de vídeos (tamaño + SHA-256 + ffprobe, §4.6)."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from tikdown_rs.core.db import create_async_engine_wal
        from tikdown_rs.models.models import Video
        from tikdown_rs.services.integrity import verify_video

        engine = create_async_engine_wal(_db_url(settings))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        stmt = select(Video)
        if username:
            stmt = stmt.where(Video.account_id.is_not(None))
        async with maker() as s:
            videos = list((await s.execute(stmt)).scalars().all())
        await engine.dispose()

        if not videos:
            print("(sin vídeos)")
            return
        ok_count = 0
        for v in videos:
            if v.local_path is None:
                print(f"! {v.tiktok_video_id} sin ruta local")
                continue
            result = verify_video(Path(v.local_path))
            if result["ok"]:
                ok_count += 1
                print(f"OK {v.tiktok_video_id} sha={result['sha256'][:12]}...")
            else:
                print(f"ERROR {v.tiktok_video_id} {result.get('reason')}")
        print(f"RESULTADO {ok_count}/{len(videos)} ok")

    asyncio.run(_go())


@app.command("last")
def last(n: int = typer.Argument(5, help="N últimos vídeos")) -> None:
    """Últimos N vídeos descargados."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from tikdown_rs.core.db import create_async_engine_wal
        from tikdown_rs.models.models import Video

        engine = create_async_engine_wal(_db_url(settings))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            rows = list(
                (await s.execute(select(Video).order_by(Video.downloaded_at.desc()).limit(n)))
                .scalars()
                .all()
            )
        await engine.dispose()
        for v in rows:
            print(f"- {v.tiktok_video_id} {v.downloaded_at or '-'}")

    asyncio.run(_go())

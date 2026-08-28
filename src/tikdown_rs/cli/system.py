"""Comandos CLI del grupo system — cli/system.py (§3).

system disk [--resume] — uso de disco, alertas, estado de downloads_paused.

story: e07s02
"""

from __future__ import annotations

import asyncio

import typer

from tikdown_rs.core.config import Settings

app = typer.Typer(name="system")


def _db_url(settings: Settings) -> str:
    return f"sqlite+aiosqlite:///{settings.data_dir / 'tikdown-rs.db'}"


@app.command("disk")
def disk(
    resume: bool = typer.Option(False, "--resume", help="Forzar reanudación manual (T45)"),
) -> None:
    """Uso de disco, alertas y estado de downloads_paused."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from tikdown_rs.core.db import create_async_engine_wal
        from tikdown_rs.core.disk import free_percent, set_downloads_paused
        from tikdown_rs.models.models import DaemonState

        if resume:
            engine = create_async_engine_wal(_db_url(settings))
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as s:
                await set_downloads_paused(s, False)  # T37: commit interno
            await engine.dispose()
            print("OK downloads reanudados (flag limpio)")
            return

        percent = free_percent(settings.data_dir)
        engine = create_async_engine_wal(_db_url(settings))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            row = (await s.execute(select(DaemonState))).scalar_one_or_none()
        await engine.dispose()
        paused = row.downloads_paused if row else False
        threshold = settings.disk_warning_free_percent
        status = "PAUSADO (disco)" if paused else "activo"
        print(f"disco: {percent:.1f}% libre (umbral {threshold}%)")
        print(f"downloads_paused: {paused} -> {status}")

    asyncio.run(_go())

@app.command("backup")
def backup() -> None:
    """Snapshot consistente en caliente de la DB (VACUUM INTO, F-21b)."""
    settings = Settings(_env_file=None)

    def _go():
        from tikdown_rs.services.backup import create_backup

        snapshot = create_backup(settings)
        print(f"OK backup: {snapshot}")

    asyncio.run(_go())

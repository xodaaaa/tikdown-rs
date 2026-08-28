"""Comandos CLI del grupo monitor — cli/monitor.py (§3).

start/stop escriben monitor_running en daemon_state (helpers T37 con commit
interno); el heartbeat del daemon aplica el cambio en caliente.

story: e03s02
"""

from __future__ import annotations

import asyncio

import typer
from sqlalchemy.ext.asyncio import async_sessionmaker

from tikdown_rs.core.config import Settings
from tikdown_rs.core.daemon_state import set_monitor_running
from tikdown_rs.core.db import create_async_engine_wal

app = typer.Typer(name="monitor")


def _db_url(settings: Settings) -> str:
    return f"sqlite+aiosqlite:///{settings.data_dir / 'tikdown-rs.db'}"


@app.command("start")
def start() -> None:
    """Inicia el ciclo del monitor (requiere daemon vivo)."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        engine = create_async_engine_wal(_db_url(settings))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            await set_monitor_running(s, True)  # T37: commit interno
        await engine.dispose()
        print("OK monitor start (el daemon lo aplica en el siguiente heartbeat)")

    asyncio.run(_go())


@app.command("stop")
def stop() -> None:
    """Detiene el ciclo del monitor."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        engine = create_async_engine_wal(_db_url(settings))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            await set_monitor_running(s, False)  # T37
        await engine.dispose()
        print("OK monitor stop")

    asyncio.run(_go())

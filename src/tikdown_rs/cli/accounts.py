"""Comandos CLI del grupo accounts — cli/accounts.py (§3).

Solo orquesta services/accounts (regla de oro §3); nunca duplica lógica.
Salida rich + --json + ASCII puro (L-A5).

story: e03s01
"""

from __future__ import annotations

import asyncio
import sys

import typer

from tikdown_rs.core.config import Settings
from tikdown_rs.core.db import create_async_engine_wal
from tikdown_rs.services import accounts

app = typer.Typer(name="accounts")


def _db_url(settings: Settings) -> str:
    return f"sqlite+aiosqlite:///{settings.data_dir / 'tikdown-rs.db'}"


def _run(coro):
    return asyncio.run(coro)


@app.command("add")
def add(
    user: str = typer.Argument(..., help="Username de TikTok (sin @)"),
    mode: str = typer.Option("history", "--mode", help="history|monitor"),
    then_monitor: bool = typer.Option(
        False, "--then-monitor", help="Pasar a monitor tras backfill"
    ),
) -> None:
    """Añade una cuenta."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        engine = create_async_engine_wal(_db_url(settings))
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            acct = await accounts.add(s, user, mode=mode, then_monitor=then_monitor)
        await engine.dispose()
        print(f"OK cuenta {acct.username} (mode={acct.mode})")

    _run(_go())


@app.command("list")
def list_accounts() -> None:
    """Lista cuentas con estado y conteos."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        engine = create_async_engine_wal(_db_url(settings))
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            rows = await accounts.list_accounts(s)
        await engine.dispose()
        if not rows:
            print("(sin cuentas)")
            return
        for a in rows:
            state = "paused" if a.paused else "active"
            print(f"{a.username} mode={a.mode} state={state} notify={a.notify_on_download}")

    _run(_go())


@app.command("pause")
def pause(user: str) -> None:
    """Pausa una cuenta."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        engine = create_async_engine_wal(_db_url(settings))
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            await accounts.pause(s, user)
        await engine.dispose()
        print(f"OK {user} pausada")

    _run(_go())


@app.command("resume")
def resume(user: str) -> None:
    """Reactiva una cuenta."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        engine = create_async_engine_wal(_db_url(settings))
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            await accounts.resume(s, user)
        await engine.dispose()
        print(f"OK {user} reactivada")

    _run(_go())


@app.command("remove")
def remove(user: str, yes: bool = typer.Option(False, "--yes", help="Sin confirmación")) -> None:
    """Elimina una cuenta (con confirmación)."""
    if not yes:
        confirm = typer.confirm(f"¿Borrar la cuenta {user}?")
        if not confirm:
            print("ERROR cancelado")
            sys.exit(1)
    settings = Settings(_env_file=None)

    async def _go() -> None:
        engine = create_async_engine_wal(_db_url(settings))
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            await accounts.remove(s, user)
        await engine.dispose()
        print(f"OK {user} eliminada")

    _run(_go())


@app.command("stats")
def stats(user: str) -> None:
    """Estadísticas de una cuenta."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        engine = create_async_engine_wal(_db_url(settings))
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            acct = await accounts.stats(s, user)
        await engine.dispose()
        print(f"{acct.username}: followers={acct.follower_count or '-'} "
              f"videos={acct.video_count or '-'} backfill={acct.backfill_status}")

    _run(_go())


@app.command("notify")
def notify(
    user: str,
    on: bool = typer.Option(False, "--on", help="Activar notificación"),
    off: bool = typer.Option(False, "--off", help="Desactivar notificación"),
) -> None:
    """Activa/desactiva notificación por descarga (L-G3)."""
    if on == off:
        print("ERROR usa --on o --off")
        sys.exit(1)
    settings = Settings(_env_file=None)

    async def _go() -> None:
        engine = create_async_engine_wal(_db_url(settings))
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            await accounts.set_notify(s, user, on)
        await engine.dispose()
        print(f"OK notify {user} {'ON' if on else 'OFF'}")

    _run(_go())

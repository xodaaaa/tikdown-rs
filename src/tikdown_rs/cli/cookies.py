"""Comandos CLI del grupo cookies — cli/cookies.py (§3).

add (--keep-source), list, test <id>, remove. Solo orquesta services/cookies.

story: e05s02
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer

from tikdown_rs.core.config import Settings
from tikdown_rs.core.db import create_async_engine_wal

app = typer.Typer(name="cookies")


def _db_url(settings: Settings) -> str:
    return f"sqlite+aiosqlite:///{settings.data_dir / 'tikdown-rs.db'}"


@app.command("add")
def add(
    path: str = typer.Argument(..., help="Ruta a cookies.txt/.json"),
    keep_source: bool = typer.Option(
        False, "--keep-source", help="Conservar fuente (F-15)"
    ),
) -> None:
    """Importa cookies (cifra y guarda)."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from tikdown_rs.core.crypto import load_or_create_fernet_key
        from tikdown_rs.services import cookies

        src = Path(path)
        if not src.exists():
            print(f"ERROR archivo no encontrado: {path}")
            sys.exit(1)
        engine_db = create_async_engine_wal(_db_url(settings))
        maker = async_sessionmaker(engine_db, expire_on_commit=False)
        key = load_or_create_fernet_key(settings.data_dir / "fernet.key")
        async with maker() as s:
            cookie = await cookies.add(s, src, fernet_key=key, keep_source=keep_source)
        await engine_db.dispose()
        kept = "conservado" if keep_source else "eliminado"
        print(f"OK cookie #{cookie.id} importada (fuente {kept})")

    asyncio.run(_go())


@app.command("list")
def list_cookies() -> None:
    """Lista cookies con estado y expiración."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from tikdown_rs.models.models import Cookie

        engine_db = create_async_engine_wal(_db_url(settings))
        maker = async_sessionmaker(engine_db, expire_on_commit=False)
        async with maker() as s:
            rows = (await s.execute(select(Cookie).order_by(Cookie.id))).scalars().all()
        await engine_db.dispose()
        if not rows:
            print("(sin cookies)")
            return
        for c in rows:
            print(f"#{c.id} {c.label or '-'} state={c.validation_state} "
                  f"exp={c.expiration_date or '-'}")

    asyncio.run(_go())


@app.command("remove")
def remove(cookie_id: int) -> None:
    """Elimina una cookie."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from tikdown_rs.models.models import Cookie

        engine_db = create_async_engine_wal(_db_url(settings))
        maker = async_sessionmaker(engine_db, expire_on_commit=False)
        async with maker() as s:
            await s.execute(delete(Cookie).where(Cookie.id == cookie_id))
            await s.commit()
        await engine_db.dispose()
        print(f"OK cookie #{cookie_id} eliminada")

    asyncio.run(_go())

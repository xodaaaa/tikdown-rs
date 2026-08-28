"""Comandos CLI del grupo backfill — cli/backfill.py (§3).

run (foreground), status. Solo orquesta services/backfill (regla de oro §3).

story: e04s02
"""

from __future__ import annotations

import asyncio
import sys

import typer

from tikdown_rs.core.config import Settings
from tikdown_rs.core.db import create_async_engine_wal

app = typer.Typer(name="backfill")


def _db_url(settings: Settings) -> str:
    return f"sqlite+aiosqlite:///{settings.data_dir / 'tikdown-rs.db'}"


@app.command("run")
def run(user: str) -> None:
    """Ejecuta un backfill foreground."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from tikdown_rs.core.crypto import decrypt_cookie, load_or_create_fernet_key
        from tikdown_rs.core.download_engine import YtDlpEngine
        from tikdown_rs.services import accounts
        from tikdown_rs.services.backfill import NoCookiesError, run_backfill
        from tikdown_rs.services.cookies import working_cookies_list

        engine_db = create_async_engine_wal(_db_url(settings))
        maker = async_sessionmaker(engine_db, expire_on_commit=False)
        async with maker() as s:
            acct = await accounts.stats(s, user)
            # F-01: cookies working — la CLI las carga del servicio y las pasa
            # descifradas al engine (bug #7): sin sesión, TikTok bloquea el feed.
            cookies = await working_cookies_list(s)
            blob = None
            if cookies:
                key = load_or_create_fernet_key(settings.data_dir / "fernet.key")
                blob = decrypt_cookie(cookies[0].encrypted_blob, key)
            # bug #14: impersonate rompe la descarga — engine sin targets
            # (descarga limpia con cookies + formato single)
            downloader = YtDlpEngine(cookies_blob=blob)
            try:
                outcome = await run_backfill(s, user, engine=downloader, cookies=cookies)
                print(f"OK backfill {user}: {outcome} (total={acct.backfill_total})")
            except NoCookiesError:
                print("ERROR backfill.no_cookies: añade cookies con 'cookies add'")
                sys.exit(1)
        await engine_db.dispose()

    asyncio.run(_go())


@app.command("status")
def status(user: str) -> None:
    """Progreso del backfill de una cuenta."""
    settings = Settings(_env_file=None)

    async def _go() -> None:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from tikdown_rs.models.models import MonitoredAccount

        engine_db = create_async_engine_wal(_db_url(settings))
        maker = async_sessionmaker(engine_db, expire_on_commit=False)
        async with maker() as s:
            row = (
                await s.execute(select(MonitoredAccount).where(MonitoredAccount.username == user))
            ).scalar_one_or_none()
        await engine_db.dispose()
        if row is None:
            print(f"ERROR cuenta no encontrada: {user}")
            sys.exit(1)
        print(
            f"{user}: status={row.backfill_status} "
            f"done={row.backfill_done}/{row.backfill_total} "
            f"cursor={row.backfill_cursor or '-'}"
        )

    asyncio.run(_go())

"""Comandos CLI del grupo daemon — cli/daemon.py.

status (T19), healthcheck (T50/R10), stop (T37), selfcheck.

story: e02s04
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tikdown_rs.core.config import Settings
from tikdown_rs.core.daemon_state import set_stop_requested
from tikdown_rs.core.db import create_async_engine_wal
from tikdown_rs.core.verify import (
    selfcheck_ffmpeg,
    selfcheck_impersonation,
    ytdlp_version_internal,
)
from tikdown_rs.models.models import DaemonState

app = typer.Typer(name="daemon")


def _db_url(settings: Settings) -> str:
    return f"sqlite+aiosqlite:///{settings.data_dir / 'tikdown-rs.db'}"


@app.command("status")
def status() -> None:
    """Estado del daemon: heartbeat, selfcheck, tareas, contención (T19)."""
    settings = Settings(_env_file=None)

    async def _run() -> None:
        engine = create_async_engine_wal(_db_url(settings))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            row = (await s.execute(select(DaemonState))).scalar_one_or_none()
        await engine.dispose()
        if row is None:
            print("ERROR daemon no inicializado (sin heartbeat)")
            sys.exit(1)
        print(f"daemon_pid: {row.daemon_pid or '-'}")
        print(f"heartbeat: {row.last_heartbeat_at or '-'}")
        print(f"monitor_running: {row.monitor_running}")
        print(f"stop_requested: {row.stop_requested}")
        print(f"last_selfcheck_ok: {row.last_selfcheck_ok}")
        # Contención leída de daemon_state (T19), nunca del proceso CLI
        print(f"db_busy_count_5min: {row.db_busy_count_5min}")

    asyncio.run(_run())


@app.command("healthcheck")
def healthcheck() -> None:
    """Healthcheck Docker: heartbeat fresco <= 3x intervalo (T50).

    NO ejecuta migraciones ni toma .migrate.lock (R10). NO selfcheck completo.
    """
    settings = Settings(_env_file=None)

    def _fresh() -> bool:
        import sqlite3

        db = settings.data_dir / "tikdown-rs.db"
        if not db.exists():
            return False  # sin DB → unhealthy (R10: no migrar)
        conn = sqlite3.connect(db)
        try:
            row = conn.execute("SELECT last_heartbeat_at FROM daemon_state WHERE id = 1").fetchone()
        except sqlite3.OperationalError:
            conn.close()
            return False  # esquema ausente → unhealthy sin migrar (R10)
        conn.close()
        if row is None or row[0] is None:
            return False
        ts = datetime.fromisoformat(row[0])
        age = (datetime.now(UTC) - ts).total_seconds()
        return age <= 3 * settings.heartbeat_interval_seconds  # T50

    sys.exit(0 if _fresh() else 1)


@app.command("stop")
def stop() -> None:
    """Pide apagado limpio escribiendo stop_requested (T37)."""
    settings = Settings(_env_file=None)

    async def _run() -> None:
        engine = create_async_engine_wal(_db_url(settings))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            await set_stop_requested(s, True)  # commit interno (T37)
        await engine.dispose()
        print("OK stop_requested")

    asyncio.run(_run())


@app.command("selfcheck")
def selfcheck() -> None:
    """Selfcheck completo bajo demanda (T6/T46)."""
    try:
        selfcheck_impersonation()
        selfcheck_ffmpeg()
    except SystemExit as exc:
        print(f"ERROR selfcheck falló (exit {exc.code})")
        sys.exit(exc.code or 1)
    print(f"OK selfcheck (yt-dlp {ytdlp_version_internal()})")

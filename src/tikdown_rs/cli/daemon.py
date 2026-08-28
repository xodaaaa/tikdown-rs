"""Comandos CLI del grupo daemon — cli/daemon.py.

status (T19), healthcheck (T50/R10), stop (T37), selfcheck.

story: e02s04
"""

from __future__ import annotations

import asyncio
import sys

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
    """Estado del daemon: heartbeat, cookies, disco, errores, contención (§3)."""
    settings = Settings(_env_file=None)

    async def _run() -> None:
        engine = create_async_engine_wal(_db_url(settings))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            from tikdown_rs.services.status import collect_status

            row = (await s.execute(select(DaemonState))).scalar_one_or_none()
            if row is None:
                print("ERROR daemon no inicializado (sin heartbeat)")
                sys.exit(1)
            st = await collect_status(s, settings)
        await engine.dispose()
        print(f"daemon_pid: {row.daemon_pid or '-'}")
        print(f"heartbeat: {row.last_heartbeat_at or '-'}")
        print(f"monitor_running: {row.monitor_running}")
        print(f"stop_requested: {row.stop_requested}")
        print(f"last_selfcheck_ok: {row.last_selfcheck_ok}")
        # §3/e15s01: métricas ampliadas
        c = st["cookies"]
        print(f"cookies: {c['valid']} validas, {c['invalid']} invalidas, {c['expiring']} expirando")
        d = st["disk"]
        alert = "ALERTA" if d["alert"] else "OK"
        print(f"disco: {d['free_percent']:.1f}% libre (umbral {d['warning_threshold']}%) [{alert}]")
        if st["recent_errors"]:
            print("ultimos errores:")
            for e in st["recent_errors"]:
                print(f"  {e['timestamp']} [{e['category']}] @{e['account']}: {e['message']}")
        else:
            print("ultimos errores: ninguno")
        # Contención leída de daemon_state (T19), nunca del proceso CLI
        print(f"db_busy_count_5min: {st['contention']['db_busy_count_5min']}")

    asyncio.run(_run())


@app.command("healthcheck")
def healthcheck() -> None:
    """Healthcheck Docker: heartbeat fresco (T50) + cookies + disco + errores (e15s01).

    NO ejecuta migraciones ni toma .migrate.lock (R10). LIGERO (§22.1): sin
    validaciones de red ni selfcheck pesado. Exit 0/1.
    """
    settings = Settings(_env_file=None)

    async def _run() -> bool:
        engine = create_async_engine_wal(_db_url(settings))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as s:
                from tikdown_rs.services.status import healthcheck_status

                ok, reasons = await healthcheck_status(s, settings)
                if not ok:
                    for r in reasons:
                        print(f"unhealthy: {r}")
                return ok
        finally:
            await engine.dispose()

    ok = asyncio.run(_run())
    sys.exit(0 if ok else 1)


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

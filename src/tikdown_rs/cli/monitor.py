"""`tikdown-rs monitor` group: start, stop (2 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII
help AND output), T-CLI-4 (run_or_exit funneling), 4.1 (a degraded daemon
rejects `monitor start` via the service gate), T-ASYNC-3 (one asyncio.run
per invocation). Regla: 10.1, 10.2.

Both commands delegate to services/monitor_state (the same functions the M5
bot calls); the CLI layer only prepares the invocation and prints ASCII.
"""

import asyncio

import typer

from tikdown_rs.cli.common import prepare_invocation, run_or_exit
from tikdown_rs.core.config import load_settings
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.services.monitor_state import start_monitor, stop_monitor

app = typer.Typer(help="Monitor loop control commands.")


def _start() -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            await start_monitor(make_session_factory(engine))
        finally:
            await engine.dispose()
        typer.echo("monitor started")

    asyncio.run(impl())


@app.command()
def start() -> None:
    """Start the monitor loop (refused while the daemon is degraded)."""
    run_or_exit(_start)


def _stop() -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            await stop_monitor(make_session_factory(engine))
        finally:
            await engine.dispose()
        typer.echo("monitor stopped")

    asyncio.run(impl())


@app.command()
def stop() -> None:
    """Stop the monitor loop."""
    run_or_exit(_stop)

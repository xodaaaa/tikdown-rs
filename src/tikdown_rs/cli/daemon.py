"""`tikdown-rs daemon` group: run, stop, status, selfcheck, healthcheck (5 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help),
T-CLI-6 (stop refuses when nobody is running; watcher honors the flag),
T-ASYNC-3 (one asyncio.run per invocation). Regla: 10.1, 10.2, 11.2.
"""

import asyncio
from datetime import UTC, datetime

import typer
from sqlalchemy.exc import OperationalError

from tikdown_rs.cli.common import prepare_invocation, raise_unimplemented, run_or_exit
from tikdown_rs.core.config import load_settings
from tikdown_rs.core.daemon_state import read_status, request_daemon_stop
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.daemon.run import run_daemon_lifecycle

app = typer.Typer(help="Daemon lifecycle and supervision commands.")


def _heartbeat_age_seconds(last_heartbeat_at: str | None) -> float | None:
    """Seconds since the last heartbeat, or None when unknown."""
    if not last_heartbeat_at:
        return None
    last = datetime.fromisoformat(last_heartbeat_at)
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (datetime.now(UTC) - last).total_seconds()


def _run() -> None:
    """One single asyncio.run for the whole lifecycle (T-ASYNC-3)."""
    try:
        asyncio.run(run_daemon_lifecycle(load_settings()))
    except KeyboardInterrupt:
        # Windows/POSIX fallback: Ctrl+C already routed the lifecycle coroutine
        # through its finally-block shutdown path (daemon/run.py docstring).
        pass


@app.command()
def run() -> None:
    """Run the TikDown-rs daemon (container CMD)."""
    run_or_exit(_run)


def _stop() -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            # T-DB-15: short, explicit session; the helper commits internally.
            async with make_session_factory(engine)() as session:
                await request_daemon_stop(session)
        finally:
            await engine.dispose()

    asyncio.run(impl())


@app.command()
def stop() -> None:
    """Request a graceful shutdown of the running daemon."""
    run_or_exit(_stop)


def _print_status(row) -> None:
    """10.1 M0 subset: plain ASCII key: value lines (T-CLI-1: no Rich markup)."""
    age = _heartbeat_age_seconds(row.last_heartbeat_at)
    if age is None:
        typer.echo("heartbeat_age_seconds: unknown")
    else:
        typer.echo(f"heartbeat_age_seconds: {age:.0f}")
    typer.echo(f"monitor_running: {int(bool(row.monitor_running))}")
    typer.echo(f"daemon_pid: {row.daemon_pid if row.daemon_pid is not None else 'none'}")


def _status() -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            async with make_session_factory(engine)() as session:
                row = await read_status(session)
        finally:
            await engine.dispose()
        if row is None:
            raise ConfigurationError(
                "No daemon_state row found; run 'tikdown-rs daemon run' at least once."
            )
        _print_status(row)

    asyncio.run(impl())


@app.command()
def status() -> None:
    """Show heartbeat, monitor state, selfcheck result and derived errors."""
    run_or_exit(_status)


@app.command()
def selfcheck() -> None:
    """Run a full self-check and record the result in daemon_state."""
    raise_unimplemented("daemon selfcheck")


def _healthcheck() -> None:
    """Lightweight probe: heartbeat freshness <= 3x interval (10.1, 11.2).

    5.4 exemption: no migrations, no lock. A missing table means the daemon
    never ran: exit 1, not a traceback.
    """

    async def impl() -> None:
        settings = load_settings()
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            try:
                async with make_session_factory(engine)() as session:
                    row = await read_status(session)
            except OperationalError:
                row = None
        finally:
            await engine.dispose()

        if row is None or row.last_heartbeat_at is None:
            raise ConfigurationError("healthcheck failed: no daemon_state row or no heartbeat")
        age = _heartbeat_age_seconds(row.last_heartbeat_at)
        threshold = 3 * settings.heartbeat_interval_seconds
        if age is None or age > threshold:
            raise ConfigurationError(
                f"healthcheck failed: heartbeat stale (age {age or -1:.0f}s > {threshold}s)"
            )

    asyncio.run(impl())


@app.command()
def healthcheck() -> None:
    """Lightweight, network-free health probe for Docker HEALTHCHECK."""
    run_or_exit(_healthcheck)

"""`tikdown-rs daemon` group: run, stop, status, selfcheck, healthcheck (5 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help),
T-CLI-6 (stop refuses when nobody is running; watcher honors the flag),
T-ASYNC-3 (one asyncio.run per invocation), T-DB-14 (contention read from
daemon_state, never the CLI process), T-ASYNC-14 (supervised tasks / zombie
threads exposed honestly), 4.1 (healthcheck fails while degraded). Regla:
10.1, 10.2, 11.2.

La regla de oro (§10.2): the real `daemon status` logic lives in
services/status.py; this module only orchestrates (prepare_invocation ->
gather -> print).
"""

import asyncio

import typer
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from tikdown_rs.cli.common import prepare_invocation, run_or_exit
from tikdown_rs.core import disk
from tikdown_rs.core.config import load_settings
from tikdown_rs.core.daemon_state import read_status, request_daemon_stop
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.daemon.run import run_daemon_lifecycle
from tikdown_rs.models import Cookie
from tikdown_rs.services.selfcheck import run_selfcheck
from tikdown_rs.services.status import (
    format_status_lines,
    gather_status,
    heartbeat_age_seconds,
    status_from_row,
)

# ponytail: the 4.1 degraded GATE for `monitor start` / `backfill run` lives
# in services/monitor_state (degraded_error) and cli/backfill (_ensure_not_degraded);
# this module only SHOWS the state (status/healthcheck).

app = typer.Typer(help="Daemon lifecycle and supervision commands.")


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


def _format_status_lines(
    row,
    cookie_counts: dict[str, int],
    supervised_count: int | None = None,
    zombie_count: int | None = None,
    recent_error_lines: list[str] | None = None,
) -> list[str]:
    """Compatibility seam: tests/test_monitor_state.py pins this entry point.

    The logic lives in services.status (status_from_row + format_status_lines).
    """
    status = status_from_row(
        row,
        cookie_counts,
        supervised_tasks=supervised_count,
        ytdlp_zombie_threads=zombie_count,
        recent_error_lines=recent_error_lines,
    )
    return format_status_lines(status)


def _status() -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            async with make_session_factory(engine)() as session:
                status = await gather_status(session)
        finally:
            await engine.dispose()
        if status is None:
            raise ConfigurationError(
                "No daemon_state row found; run 'tikdown-rs daemon run' at least once."
            )
        for line in format_status_lines(status):
            typer.echo(line)

    asyncio.run(impl())


@app.command()
def status() -> None:
    """Show heartbeat, monitor state, selfcheck result and derived errors."""
    run_or_exit(_status)


@app.command()
def selfcheck() -> None:
    """Run a full self-check and record the result in daemon_state."""
    run_or_exit(_selfcheck)


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
                    # 10.1 cookie threshold over the RUNTIME usable set
                    # (get_working_cookie, T-COOKIES-4): valid OR inconclusive;
                    # only 'invalid' is excluded (audit 2.1).
                    usable_cookies = (
                        await session.execute(
                            select(func.count())
                            .select_from(Cookie)
                            .where(Cookie.validation_state != "invalid")
                        )
                    ).scalar_one()
            except OperationalError:
                row = None
                usable_cookies = 0
        finally:
            await engine.dispose()

        if row is None or row.last_heartbeat_at is None:
            raise ConfigurationError("healthcheck failed: no daemon_state row or no heartbeat")
        age = heartbeat_age_seconds(row.last_heartbeat_at)
        threshold = 3 * settings.heartbeat_interval_seconds
        if age is None or age > threshold:
            raise ConfigurationError(
                f"healthcheck failed: heartbeat stale (age {age or -1:.0f}s > {threshold}s)"
            )
        # 4.1/10.1: a degraded daemon cannot download at all -> unhealthy, with
        # the cause named so the operator lands on the selfcheck fix path.
        if row.degraded_reason:
            raise ConfigurationError(
                f"healthcheck failed: daemon degraded ({row.degraded_reason}); "
                "run 'tikdown-rs daemon selfcheck' for details"
            )
        if usable_cookies < 1:
            raise ConfigurationError(
                "healthcheck failed: cookies unhealthy (no usable cookie (valid or inconclusive))"
            )
        if not disk.is_disk_ok(settings.data_dir, settings.disk_warning_free_percent):
            free = disk.free_percent(settings.data_dir)
            raise ConfigurationError(
                f"healthcheck failed: disk unhealthy (free {free:.1f}% <= "
                f"threshold {settings.disk_warning_free_percent}% on {settings.data_dir})"
            )

    asyncio.run(impl())


@app.command()
def healthcheck() -> None:
    """Lightweight, network-free health probe for Docker HEALTHCHECK."""
    run_or_exit(_healthcheck)


def _selfcheck() -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            result = await run_selfcheck(settings, make_session_factory(engine))
        finally:
            await engine.dispose()

        typer.echo(
            f"impersonation: {result.impersonation_reason} "
            f"(targets: {result.impersonation_target_count})"
        )
        if result.ffmpeg_found and result.ffprobe_found:
            typer.echo("binaries: ok")
        else:
            missing = "ffmpeg" if not result.ffmpeg_found else "ffprobe"
            typer.echo(f"binaries: {missing} not found (hard dependency, T-DEPLOY-10)")
        typer.echo(f"data_dir: {'ok' if result.data_dir_writable else 'not writable'}")
        typer.echo(f"overall: {'ok' if result.ok else f'degraded ({result.degraded_reason})'}")
        if not result.ok:
            raise typer.Exit(1)

    asyncio.run(impl())

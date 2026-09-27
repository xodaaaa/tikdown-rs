"""`tikdown-rs daemon` group: run, stop, status, selfcheck, healthcheck (5 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help),
T-CLI-6 (stop refuses when nobody is running; watcher honors the flag),
T-ASYNC-3 (one asyncio.run per invocation), T-DB-14 (contention read from
daemon_state, never the CLI process), T-ASYNC-14 (supervised tasks / zombie
threads exposed honestly), 4.1 (healthcheck fails while degraded). Regla:
10.1, 10.2, 11.2.
"""

import asyncio
from datetime import UTC, datetime

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
from tikdown_rs.models import Cookie, Video
from tikdown_rs.services.selfcheck import run_selfcheck

# ponytail: the 4.1 degraded GATE for `monitor start` / `backfill run` lives
# in services/monitor_state (degraded_error) and cli/backfill (_ensure_not_degraded);
# this module only SHOWS the state (status/healthcheck).

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


_RECENT_ERROR_LIMIT = 5  # 10.1: 'ultimos errores', a bounded tail
_ERROR_MESSAGE_MAX_CHARS = 120


def _format_status_lines(
    row,
    cookie_counts: dict[str, int],
    supervised_count: int | None = None,
    zombie_count: int | None = None,
    recent_error_lines: list[str] | None = None,
) -> list[str]:
    """10.1 status contents as plain ASCII key: value lines (T-CLI-1).

    Honesty contract (T-ASYNC-14): supervised tasks and zombie yt-dlp threads
    are IN-PROCESS counters of the daemon process. `daemon status` runs in a
    separate CLI process whose registry is always empty and whose engine
    object does not exist, so the defaults print 'n/a (in-process)' instead
    of a fake 0. A caller that DOES hold the live objects (the daemon itself
    or the M5 bot, which runs inside the daemon process) passes the real
    counts and gets real numbers.

    ponytail: persisting the counters would need new daemon_state columns
    (forbidden here: no migrations); the M5 bot or a future column can
    promote these to persisted values without changing the output format.
    """
    age = _heartbeat_age_seconds(row.last_heartbeat_at)
    if age is None:
        lines = ["heartbeat_age_seconds: unknown"]
    else:
        lines = [f"heartbeat_age_seconds: {age:.0f}"]
    lines.append(f"monitor_running: {int(bool(row.monitor_running))}")
    lines.append(f"daemon_pid: {row.daemon_pid if row.daemon_pid is not None else 'none'}")
    # T-DB-14: the contention window is read from daemon_state (persisted by
    # the heartbeat), NEVER from this process (a CLI process always sees 0).
    lines.append(f"db_busy_count_5min: {row.db_busy_count_5min}")
    lines.append(
        f"supervised_tasks: {supervised_count if supervised_count is not None else 'n/a (in-process)'}"
    )
    lines.append(
        f"ytdlp_zombie_threads: {zombie_count if zombie_count is not None else 'n/a (in-process)'}"
    )
    lines.append(f"cookies_valid: {cookie_counts.get('valid', 0)}")
    lines.append(f"cookies_invalid: {cookie_counts.get('invalid', 0)}")
    lines.append(f"cookies_inconclusive: {cookie_counts.get('inconclusive', 0)}")
    lines.append(f"last_selfcheck_at: {row.last_selfcheck_at or 'none'}")
    lines.append(
        "last_selfcheck_ok: "
        + (str(int(row.last_selfcheck_ok)) if row.last_selfcheck_ok is not None else "unknown")
    )
    lines.append(f"degraded_reason: {row.degraded_reason or 'none'}")
    lines.extend(recent_error_lines if recent_error_lines is not None else ["recent_errors: none"])
    return lines


def _print_status(row, cookie_counts: dict[str, int], recent_error_lines: list[str]) -> None:
    """Echo the formatted status lines (10.1)."""
    for line in _format_status_lines(row, cookie_counts, recent_error_lines=recent_error_lines):
        typer.echo(line)


async def _recent_error_lines(session) -> list[str]:
    """10.1: last failed videos derived from the `videos` table, NO new table.

    A separate CLI process cannot reach the daemon's log stream, so the DB
    trace is the honest shared source: error_category + truncated message.
    """
    rows = (
        (
            await session.execute(
                select(Video)
                .where(Video.status == "failed")
                .order_by(Video.updated_at.desc())
                .limit(_RECENT_ERROR_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return ["recent_errors: none"]
    lines = [f"recent_errors: {len(rows)}"]
    for video in rows:
        message = (video.error_message or "-")[:_ERROR_MESSAGE_MAX_CHARS]
        lines.append(
            f"recent_error: {video.tiktok_video_id} [{video.error_category or '-'}] {message}"
        )
    return lines


def _status() -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            async with make_session_factory(engine)() as session:
                row = await read_status(session)
                cookie_counts = dict(
                    (
                        await session.execute(
                            select(Cookie.validation_state, func.count()).group_by(
                                Cookie.validation_state
                            )
                        )
                    ).all()
                )
                recent_error_lines = await _recent_error_lines(session)
        finally:
            await engine.dispose()
        if row is None:
            raise ConfigurationError(
                "No daemon_state row found; run 'tikdown-rs daemon run' at least once."
            )
        _print_status(row, cookie_counts, recent_error_lines)

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
                    # 10.1 binary cookie threshold: strict, >= 1 VALID cookie.
                    valid_cookies = (
                        await session.execute(
                            select(func.count())
                            .select_from(Cookie)
                            .where(Cookie.validation_state == "valid")
                        )
                    ).scalar_one()
            except OperationalError:
                row = None
                valid_cookies = 0
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
        # 4.1/10.1: a degraded daemon cannot download at all -> unhealthy, with
        # the cause named so the operator lands on the selfcheck fix path.
        if row.degraded_reason:
            raise ConfigurationError(
                f"healthcheck failed: daemon degraded ({row.degraded_reason}); "
                "run 'tikdown-rs daemon selfcheck' for details"
            )
        if valid_cookies < 1:
            raise ConfigurationError(
                "healthcheck failed: cookies unhealthy (no cookie with validation_state='valid')"
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

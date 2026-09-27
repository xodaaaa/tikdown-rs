"""`tikdown-rs backfill` group: run, status, cancel, retry-failed (4 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help),
T-CLI-4 (run_or_exit error funneling), T-BACKFILL-12 (real cookies at THIS entry
point, never a hardcoded empty list), T-BACKFILL-19 (--queue refuses a running
backfill), 4.1 (a degraded daemon REJECTS `backfill run` before anything else,
including the cookie gate). Regla: 10.1, 10.2, 9.1, 9.3, 9.4, 9.6.
"""

import asyncio
from pathlib import Path

import typer

import tikdown_rs.services.backfill as backfill_service
from tikdown_rs.cli.common import prepare_invocation, run_or_exit
from tikdown_rs.core.archive import DownloadArchive
from tikdown_rs.core.config import Settings, load_settings
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.download_engine import YtDlpEngine
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.core.pacing import DownloadPacer, DownloadSemaphore
from tikdown_rs.models import DaemonState
from tikdown_rs.services.accounts import get_account
from tikdown_rs.services.backfill_ops import (
    backfill_status_view,
    cancel_backfill,
    queue_backfill,
    retry_failed,
)
from tikdown_rs.services.cookies import get_working_cookie
from tikdown_rs.services.monitor_state import degraded_error

app = typer.Typer(help="History backfill queue commands.")


async def _ensure_not_degraded(factory) -> None:
    """4.1 gate: refuse the whole command while daemon_state is degraded.

    Runs BEFORE anything else (before queue_backfill AND before the cookie
    gate): a degraded daemon cannot download, so queueing would create work
    nobody can execute. The error names the cause and the selfcheck fix path.
    """
    async with factory() as session:
        state = await session.get(DaemonState, 1)
    if state is not None and state.degraded_reason:
        raise degraded_error(state.degraded_reason)


@app.command()
def run(
    user: str = typer.Argument(..., help="Account handle, e.g. @user."),
    queue: bool = typer.Option(
        False,
        "--queue",
        help="Enqueue the backfill instead of running it inline.",
    ),
) -> None:
    """Run (or enqueue) a history backfill for an account."""
    run_or_exit(_run, user, queue)


def _run(user: str, queue: bool) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            factory = make_session_factory(engine)
            await _ensure_not_degraded(factory)  # 4.1: gate BEFORE cookies/queue
            await queue_backfill(factory, user, queue)
            if queue:
                # The daemon's collect job picks it up (M4 wiring).
                typer.echo(
                    f"backfill queued: {user.lstrip('@').lower()} (the daemon will collect it)"
                )
                return
            # Foreground run (9.1): real cookies loaded at THIS entry point;
            # the engine REQUIRES a non-empty blob (T-BACKFILL-12, never []).
            cookie = await get_working_cookie(factory)
            if cookie is None:
                raise ConfigurationError(
                    "backfill.no_cookies: no working cookie (add one with `tikdown-rs cookies add`)"
                )
            account = await get_account(factory, user)
            if account is None:  # pragma: no cover - queue_backfill validated it
                raise ConfigurationError(f"unknown account: {user}")
            final_status = await backfill_service.run_backfill(
                factory,
                account.id,
                engine=YtDlpEngine(cookie.cookie_blob, settings),
                pacer=DownloadPacer(factory, settings),
                semaphore=DownloadSemaphore(settings.max_concurrent_downloads),
                archive=DownloadArchive(Path(settings.data_dir) / "download_archive.txt", factory),
            )
            view = await backfill_status_view(factory, user)
            typer.echo(
                f"backfill finished: status={final_status} "
                f"total={view['backfill_total']} done={view['backfill_done']}"
            )
            if final_status not in ("completed", "cancelled", "paused"):
                raise typer.Exit(1)
        finally:
            await engine.dispose()

    asyncio.run(impl())


@app.command()
def status(user: str = typer.Argument(..., help="Account handle, e.g. @user.")) -> None:
    """Show backfill progress for an account."""
    run_or_exit(_status, user)


def _status(user: str) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            view = await backfill_status_view(make_session_factory(engine), user)
        finally:
            await engine.dispose()
        # T-CLI-1: plain ASCII key: value lines, no Rich markup.
        typer.echo(f"username: {view['username']}")
        typer.echo(f"backfill_status: {view['backfill_status']}")
        typer.echo(f"backfill_total: {view['backfill_total']}")
        typer.echo(f"backfill_done: {view['backfill_done']}")
        typer.echo(f"backfill_cursor: {view['backfill_cursor'] or '-'}")
        typer.echo(f"pause_reason: {view['pause_reason'] or '-'}")
        typer.echo(f"needs_review: {view['needs_review']}")

    asyncio.run(impl())


@app.command()
def cancel(user: str = typer.Argument(..., help="Account handle, e.g. @user.")) -> None:
    """Cancel a running or queued backfill."""
    run_or_exit(_cancel, user)


def _cancel(user: str) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            await cancel_backfill(make_session_factory(engine), user)
        finally:
            await engine.dispose()
        typer.echo(f"backfill cancelled: {user.lstrip('@').lower()}")

    asyncio.run(impl())


@app.command("retry-failed")
def retry_failed_cmd(
    user: str | None = typer.Argument(
        None,
        help="Account handle, e.g. @user.",
    ),
    all: bool = typer.Option(False, "--all", help="Retry failed videos for all accounts."),
) -> None:
    """Retry failed videos for one account or all accounts with --all."""
    run_or_exit(_retry_failed, user, all)


def _retry_failed(user: str | None, all_accounts: bool) -> None:
    async def impl() -> None:
        settings: Settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            factory = make_session_factory(engine)
            archive = DownloadArchive(Path(settings.data_dir) / "download_archive.txt", factory)
            count = await retry_failed(factory, user, all_accounts, archive=archive)
        finally:
            await engine.dispose()
        scope = "all accounts" if all_accounts else user.lstrip("@").lower()
        typer.echo(f"retry-failed: requeued {count} videos for {scope}")

    asyncio.run(impl())

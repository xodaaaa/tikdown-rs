"""`tikdown-rs accounts` group: add, list, pause, resume, notify, remove, check, stats (8).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help),
T-CLI-4 (run_or_exit error funneling), T-BACKFILL-1. Regla: 10.1, 10.2.
Deletion verb is `remove`, never `delete`. `check` stays a loud stub: it needs
the engine listing round (T-ENGINE-19); `stats` serves the M6 per-account data.
"""

import asyncio

import typer

from tikdown_rs.cli.common import prepare_invocation, raise_unimplemented, run_or_exit
from tikdown_rs.core.config import load_settings
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.services.accounts import (
    account_stats,
    add_account,
    list_accounts,
    remove_account,
    set_notify,
    set_paused,
)

app = typer.Typer(help="Monitored account management.")


@app.command()
def add(
    user: str = typer.Argument(..., help="Account handle, e.g. @user."),
    mode: str = typer.Option(
        "history",
        "--mode",
        help="Fetch mode: history or monitor.",
    ),
    then_monitor: bool = typer.Option(
        False,
        "--then-monitor",
        help="Switch to monitor mode after the history backfill.",
    ),
) -> None:
    """Add an account with an initial fetch mode."""
    run_or_exit(_add, user, mode, then_monitor)


def _add(user: str, mode: str, then_monitor: bool) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            account_id = await add_account(make_session_factory(engine), user, mode, then_monitor)
        finally:
            await engine.dispose()
        typer.echo(f"account added: id={account_id} user={user.lstrip('@').lower()}")

    asyncio.run(impl())


@app.command("list")
def list_cmd() -> None:
    """List monitored accounts and their state."""
    run_or_exit(_list)


def _list() -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            rows = await list_accounts(make_session_factory(engine))
        finally:
            await engine.dispose()
        if not rows:
            typer.echo("no accounts")
            return
        for row in rows:  # T-CLI-1: plain ASCII lines, no Rich markup.
            typer.echo(
                f"id={row['id']} user={row['username']} mode={row['mode']} "
                f"paused={row['paused']} review={row['needs_review']} "
                f"backfill={row['backfill_status']} "
                f"then_monitor={row['monitor_after_backfill']} "
                f"notify={row['notify_on_download']}"
            )

    asyncio.run(impl())


@app.command()
def pause(user: str = typer.Argument(..., help="Account handle, e.g. @user.")) -> None:
    """Pause monitoring for an account."""
    run_or_exit(_set_paused, user, True)


@app.command()
def resume(user: str = typer.Argument(..., help="Account handle, e.g. @user.")) -> None:
    """Resume monitoring for a paused account."""
    run_or_exit(_set_paused, user, False)


def _set_paused(user: str, paused: bool) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            await set_paused(make_session_factory(engine), user, paused)
        finally:
            await engine.dispose()
        typer.echo(f"account {'paused' if paused else 'resumed'}: {user.lstrip('@').lower()}")

    asyncio.run(impl())


@app.command()
def notify(
    user: str | None = typer.Argument(
        None,
        help="Account handle to toggle (e.g. @user); omit for ALL accounts.",
    ),
    enabled: bool = typer.Option(
        ...,
        "--on/--off",
        help="Enable or disable download notifications.",
    ),
) -> None:
    """Toggle notifications on or off (all accounts, or one with a handle)."""
    run_or_exit(_notify, user, enabled)


def _notify(user: str | None, enabled: bool) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        factory = make_session_factory(engine)
        try:
            if user is not None:
                targets = [user.lstrip("@").lower()]
            else:
                targets = [row["username"] for row in await list_accounts(factory)]
            for name in targets:
                await set_notify(factory, name, enabled)
        finally:
            await engine.dispose()
        scope = " ".join(targets) if targets else "(no accounts)"
        state = "on" if enabled else "off"
        typer.echo(f"notify {state}: {scope}")

    asyncio.run(impl())


@app.command()
def remove(user: str = typer.Argument(..., help="Account handle, e.g. @user.")) -> None:
    """Remove an account (deletion verb is always `remove`)."""
    run_or_exit(_remove, user)


def _remove(user: str) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            await remove_account(make_session_factory(engine), user)
        finally:
            await engine.dispose()
        typer.echo(f"account removed: {user.lstrip('@').lower()}")

    asyncio.run(impl())


@app.command()
def check(user: str = typer.Argument(..., help="Account handle, e.g. @user.")) -> None:
    """Verify engine and cookies work for one account."""
    raise_unimplemented("accounts check")


@app.command()
def stats() -> None:
    """Show whole-library per-account statistics (no arguments, §10.1)."""
    run_or_exit(_stats)


def _stats() -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            rows = await account_stats(make_session_factory(engine))
        finally:
            await engine.dispose()
        if not rows:
            typer.echo("no accounts")
            return
        modes: dict[str, int] = {}
        for row in rows:
            modes[row.mode] = modes.get(row.mode, 0) + 1
        by_mode = " ".join(f"{mode}={count}" for mode, count in sorted(modes.items()))
        typer.echo(f"accounts: {len(rows)} ({by_mode})")
        for row in rows:  # T-CLI-1: plain ASCII; T-DATA-10: bytes read, labeled approx.
            typer.echo(
                f"user={row.username} mode={row.mode} paused={int(row.paused)} "
                f"review={int(row.needs_review)} downloaded={row.videos_downloaded} "
                f"failed={row.videos_failed} pending={row.videos_pending} "
                f"disk_bytes={row.total_disk_bytes} (approx) "
                f"backfill={row.backfill_status} {row.backfill_done}/{row.backfill_total}"
            )

    asyncio.run(impl())

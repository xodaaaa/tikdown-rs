"""`tikdown-rs system` group: disk, backup, site render (3 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII),
T-ENGINE-27 (manual resume, 8.2). Regla: 10.1, 10.2.
"""

import asyncio

import typer

from tikdown_rs.cli.common import prepare_invocation, run_or_exit
from tikdown_rs.core import disk as disk_probe
from tikdown_rs.core.config import load_settings
from tikdown_rs.core.daemon_state import read_status, set_downloads_paused
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.notifications import render as render_message
from tikdown_rs.core.notifications.events import EVENT_DISK_RESUMED
from tikdown_rs.services.maintenance import create_backup
from tikdown_rs.services.static_site import render_site, resolve_output_dir

app = typer.Typer(help="System maintenance commands.")

site_app = typer.Typer(help="Static dashboard site commands.")
app.add_typer(site_app, name="site")


def _echo_event(event: dict) -> None:
    """Sync event channel for the CLI: render the message to stdout (ASCII)."""
    typer.echo(render_message(event["event"], event))


def _disk(resume: bool) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            async with make_session_factory(engine)() as session:
                if resume:
                    # 8.2: manual resume -- always clears, emits disk.resumed.
                    await set_downloads_paused(session, False, reason="manual")
                    row = await read_status(session)
                    _echo_event(
                        {
                            "event": EVENT_DISK_RESUMED,
                            "free_percent": disk_probe.free_percent(settings.data_dir),
                        }
                    )
                else:
                    row = await read_status(session)
        finally:
            await engine.dispose()
        typer.echo(f"free_percent: {disk_probe.free_percent(settings.data_dir):.1f}")
        typer.echo(f"warning_threshold_percent: {settings.disk_warning_free_percent}")
        paused = bool(row.downloads_paused) if row is not None else False
        typer.echo(f"downloads_paused: {int(paused)}")

    asyncio.run(impl())


@app.command()
def disk(
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Resume downloads paused by the disk watermark.",
    ),
) -> None:
    """Show disk usage and the paused-by-disk queue state."""
    run_or_exit(_disk, resume)


def _backup() -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            snapshot, deleted = await create_backup(make_session_factory(engine), settings)
        finally:
            await engine.dispose()
        typer.echo(f"backup: {snapshot}")
        typer.echo(f"retention: {deleted} old snapshot(s) removed")

    asyncio.run(impl())


@app.command()
def backup() -> None:
    """Create a data backup snapshot with retention (14.5)."""
    run_or_exit(_backup)


@site_app.command()
def render() -> None:
    """Regenerate the static dashboard site (writes files, never serves HTTP)."""
    run_or_exit(_site_render)


def _command_reference() -> dict:
    """Introspect the typer tree into JSON-safe data (10.3 Column 4).

    Lives HERE, not in services/: the 13.2 rule forbids services/* importing
    cli/, so render_site receives the reference as a plain parameter.
    Built at render time from the typer models tree (no click import: not a
    direct dependency), so it matches the registered tree by construction.
    """
    from typer.models import CommandInfo, TyperInfo

    from tikdown_rs.cli.main import app

    def command_entry(info: CommandInfo) -> dict:
        name = info.name or (info.callback.__name__ or "").replace("_", "-")
        doc = (info.callback.__doc__ or "").strip()
        return {"name": name, "help": (info.help or doc).strip()}

    def typer_entry(info: TyperInfo) -> dict:
        sub = info.typer_instance
        name = info.name or sub.info.name or ""
        entry: dict = {
            "name": name,
            "help": (info.help or sub.info.help or "").strip(),
            "commands": [command_entry(cmd) for cmd in sub.registered_commands],
        }
        for nested in sub.registered_groups:
            entry["commands"].append(typer_entry(nested))
        entry["commands"].sort(key=lambda c: c["name"])
        return entry

    return {
        "name": "tikdown-rs",
        "help": (app.info.help or "").strip(),
        "commands": sorted(
            [typer_entry(group) for group in app.registered_groups],
            key=lambda c: c["name"],
        ),
    }


def _site_render() -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            paths = await render_site(
                make_session_factory(engine),
                settings,
                resolve_output_dir(settings),
                command_reference=_command_reference(),
            )
        finally:
            await engine.dispose()
        for path in paths:
            typer.echo(f"wrote: {path}")

    asyncio.run(impl())

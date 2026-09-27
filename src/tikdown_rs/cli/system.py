"""`tikdown-rs system` group: disk, backup, site render (3 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII),
T-ENGINE-27 (manual resume, 8.2). Regla: 10.1, 10.2.
"""

import asyncio

import typer

from tikdown_rs.cli.common import prepare_invocation, raise_unimplemented, run_or_exit
from tikdown_rs.core import disk as disk_probe
from tikdown_rs.core.config import load_settings
from tikdown_rs.core.daemon_state import read_status, set_downloads_paused
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.notifications import render as render_message
from tikdown_rs.core.notifications.events import EVENT_DISK_RESUMED

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


@app.command()
def backup() -> None:
    """Create a data backup."""
    raise_unimplemented("system backup")


@site_app.command()
def render() -> None:
    """Regenerate the static dashboard site (writes files, never serves HTTP)."""
    raise_unimplemented("system site render")

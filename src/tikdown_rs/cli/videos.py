"""`tikdown-rs videos` group: last, export, integrity (3 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help),
T-CLI-3 (raw export payload), T-CLI-4 (run_or_exit funneling), T-DEPLOY-16
(CSV formula sanitization, enforced by the service). Regla: 10.1, 10.2.
§10.1 parity note: `videos last` and `videos export` consume the SAME service
callables the bot's /last uses; the CLI only orchestrates and prints.
"""

import asyncio

import typer

from tikdown_rs.cli.common import prepare_invocation, raise_unimplemented, run_or_exit
from tikdown_rs.core.config import load_settings
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.services.videos import DEFAULT_LAST_LIMIT, export_videos, recent_videos

app = typer.Typer(help="Video archive queries and exports.")


@app.command()
def last(
    n: int | None = typer.Argument(
        None,
        help="Number of videos to show (default 10, max 100).",
    ),
) -> None:
    """Show the most recent archived videos (T-CLI-1: plain ASCII lines)."""
    run_or_exit(_last, n)


def _last(n: int | None) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            rows = await recent_videos(
                make_session_factory(engine),
                limit=DEFAULT_LAST_LIMIT if n is None else n,
            )
        finally:
            await engine.dispose()
        if not rows:
            typer.echo("no videos")
            return
        for row in rows:  # T-CLI-1: plain ASCII lines, no Rich markup.
            typer.echo(
                f"id={row['id']} video={row['tiktok_video_id']} "
                f"user={row['username'] or '-'} status={row['status']} "
                f"downloaded={row['downloaded_at'] or '-'} size={row['file_size'] or 0}"
            )

    asyncio.run(impl())


@app.command()
def export(
    output_format: str = typer.Option(
        "json",
        "--format",
        help="Export format: json or csv.",
    ),
) -> None:
    """Export the video archive without markup or Rich wrapping (T-CLI-3)."""
    run_or_exit(_export, output_format)


def _export(output_format: str) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            payload = await export_videos(make_session_factory(engine), output_format)
        finally:
            await engine.dispose()
        typer.echo(payload)  # raw payload only: no echo decoration (T-CLI-3)

    asyncio.run(impl())


@app.command()
def integrity(
    username: str | None = typer.Argument(
        None,
        help="Restrict the check to one account handle.",
    ),
) -> None:
    """Check archive integrity for one account or the whole archive."""
    raise_unimplemented("videos integrity")

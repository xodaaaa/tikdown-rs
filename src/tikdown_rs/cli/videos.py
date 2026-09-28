"""`tikdown-rs videos` group: last, export, integrity (3 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help),
T-CLI-3 (raw export payload), T-CLI-4 (run_or_exit funneling), T-DEPLOY-16
(CSV formula sanitization, enforced by the service). Regla: 10.1, 10.2.
§10.1 parity note: `videos last` and `videos export` consume the SAME service
callables the bot's /last uses; the CLI only orchestrates and prints.
"""

import asyncio
import sys

import typer

from tikdown_rs.cli.common import prepare_invocation, run_or_exit
from tikdown_rs.core.config import load_settings
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.services.integrity import check_integrity
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


def _echo_payload(payload: str) -> None:
    """Write export DATA as explicit UTF-8, never the console codepage.

    T-CLI-10 (live round M6): Windows legacy consoles run cp1252 and real
    TikTok titles carry emoji/unicode — ``typer.echo`` raises
    ``UnicodeEncodeError`` and the export crashes with a traceback. Exports
    are data: UTF-8 bytes on ``sys.stdout.buffer`` (redirection yields valid
    UTF-8 files). Falls back to plain echo when stdout has no buffer.
    """
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        typer.echo(payload)
        return
    buffer.write(payload.encode("utf-8") + b"\n")
    buffer.flush()


def _export(output_format: str) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            payload = await export_videos(make_session_factory(engine), output_format)
        finally:
            await engine.dispose()
        _echo_payload(payload)  # raw payload only: no echo decoration (T-CLI-3)

    asyncio.run(impl())


@app.command()
def integrity(
    username: str | None = typer.Argument(
        None,
        help="Restrict the check to one account handle.",
    ),
) -> None:
    """Check archive integrity for one account or the whole archive (§14.5)."""
    run_or_exit(_integrity, username)


def _integrity(username: str | None) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            report = await check_integrity(make_session_factory(engine), username=username)
        finally:
            await engine.dispose()
        # §14.5 diagnostic: size + SHA-256 + ffprobe. A missing ffprobe only
        # SKIPS the container check (WARNING), and flagged rows never change
        # the exit code — failures are reported, not raised.
        typer.echo(
            f"integrity: checked={report.total_checked} ok={report.ok} "
            f"missing={report.missing} container_failed={report.container_failed} "
            f"ffprobe_skipped={report.ffprobe_skipped}"
        )
        if report.ffprobe_skipped:
            typer.echo(
                "WARNING: ffprobe not found; container checks skipped "
                "(size and SHA-256 still reported)"
            )
        for row in report.rows:  # T-CLI-1: plain ASCII lines, one per non-ok row
            if row.missing:
                typer.echo(f"MISSING id={row.tiktok_video_id} path={row.local_path}")
            elif row.container_ok is False:
                typer.echo(f"CONTAINER_FAILED id={row.tiktok_video_id} path={row.local_path}")

    asyncio.run(impl())

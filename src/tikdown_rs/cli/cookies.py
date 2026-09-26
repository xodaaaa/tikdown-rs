"""`tikdown-rs cookies` group: add, list, test, remove (4 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help),
T-CLI-4 (run_or_exit error funneling), T-DB-15 (short sessions). Regla: 10.1,
10.2, 7. Deletion verb is `remove`, never `delete`.
"""

import asyncio
from pathlib import Path

import typer

from tikdown_rs.cli.common import prepare_invocation, run_or_exit
from tikdown_rs.core.config import load_settings
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.core.verify import entries_have_video, probe_profile
from tikdown_rs.models import Cookie
from tikdown_rs.services.cookies import add_cookie, list_cookies, remove_cookie, validate_cookie

app = typer.Typer(help="Cookie store management.")


def _open_engine():
    settings = load_settings()
    return create_db_engine(sqlite_url_for(settings.data_dir))


@app.command()
def add(
    path: str = typer.Argument(..., help="Path to the cookies file."),
    keep_source: bool = typer.Option(
        False,
        "--keep-source",
        help="Keep the source file instead of deleting it after import.",
    ),
    label: str | None = typer.Option(None, "--label", help="Optional label for the store."),
) -> None:
    """Import a cookies file into the store (converted to canonical Netscape)."""
    run_or_exit(_add, path, label, keep_source)


def _add(path: str, label: str | None, keep_source: bool) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            cookie_id = await add_cookie(
                make_session_factory(engine), Path(path), label, keep_source
            )
        finally:
            await engine.dispose()
        typer.echo(f"cookie added: id={cookie_id}")

    asyncio.run(impl())


@app.command()
def list() -> None:
    """List cookies in the store with their state."""
    run_or_exit(_list)


def _list() -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            rows = await list_cookies(make_session_factory(engine))
        finally:
            await engine.dispose()
        if not rows:
            typer.echo("no cookies")
            return
        for row in rows:  # T-CLI-1: plain ASCII lines, no Rich markup.
            typer.echo(
                f"id={row.id} label={row.label or '-'} "
                f"state={row.validation_state} "
                f"last_validated_at={row.last_validated_at or '-'}"
            )

    asyncio.run(impl())


@app.command()
def test(cookie_id: int = typer.Argument(..., help="Cookies id in the store.")) -> None:
    """Validate a stored cookies file against the configured probe profiles."""
    run_or_exit(_test, cookie_id)


def _test(cookie_id: int) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        # DR-5: never a default, never a hardcoded third-party profile. An
        # empty list is an incomplete configuration, not a silent default.
        if not settings.cookie_validation_url:
            raise ConfigurationError(
                "COOKIE_VALIDATION_URL is empty: configure 2-3 of your own "
                "verified probe profile URLs (comma-separated), each checked "
                "with `yt-dlp -s <url>` before deploying"
            )
        engine = create_db_engine(sqlite_url_for(settings.data_dir))

        # Composition root: the service receives a bool probe_fn so it never
        # imports yt_dlp even transitively (§4.8 layering intent).
        def probe_fn(blob: bytes, url: str, max_entries: int) -> bool:
            return entries_have_video(probe_profile(blob, url, max_entries))

        try:
            factory = make_session_factory(engine)
            state = await validate_cookie(
                factory,
                cookie_id,
                settings.cookie_validation_url,
                probe_fn,
                settings.cookie_probe_max_entries,
            )
            reason = "-"
            if state != "inconclusive":
                async with factory() as session:
                    row = await session.get(Cookie, cookie_id)
                    if row is not None and row.last_validation_reason:
                        reason = row.last_validation_reason
        finally:
            await engine.dispose()
        typer.echo(f"cookie {cookie_id}: {state} ({reason})")  # T-CLI-1: ASCII

    asyncio.run(impl())


@app.command()
def remove(cookie_id: int = typer.Argument(..., help="Cookies id in the store.")) -> None:
    """Remove a cookies file from the store (deletion verb is `remove`)."""
    run_or_exit(_remove, cookie_id)


def _remove(cookie_id: int) -> None:
    async def impl() -> None:
        settings = await prepare_invocation(load_settings())
        engine = create_db_engine(sqlite_url_for(settings.data_dir))
        try:
            await remove_cookie(make_session_factory(engine), cookie_id)
        finally:
            await engine.dispose()
        typer.echo(f"cookie removed: id={cookie_id}")

    asyncio.run(impl())

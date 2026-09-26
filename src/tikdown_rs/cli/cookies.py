"""`tikdown-rs cookies` group: add, list, test, remove (4 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help),
T-CLI-4 (run_or_exit error funneling). Regla: 10.1, 10.2, 7.
Deletion verb is `remove`, never `delete`. `test` stays a loud stub until the
validation-probe unit (M1 next step).
"""

import asyncio
from pathlib import Path

import typer

from tikdown_rs.cli.common import prepare_invocation, raise_unimplemented, run_or_exit
from tikdown_rs.core.config import load_settings
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.services.cookies import add_cookie, list_cookies, remove_cookie

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
    """Validate a stored cookies file against the site."""
    raise_unimplemented("cookies test")


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

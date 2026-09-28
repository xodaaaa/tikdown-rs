"""Shared CLI helpers: error funneling, unimplemented stubs, per-invocation prep.

Trampas neutralizadas: T-CLI-4, T-CLI-5 (M0 loud-failure), T-CLI-1 (ASCII only),
T-ASYNC-4 (migrations off the loop thread). Regla: 10.2, 10.1, 5.4.
"""

import asyncio

import sqlalchemy.exc
import typer

from tikdown_rs.core.config import Settings
from tikdown_rs.core.errors import ConfigurationError, DownloadTimeoutError
from tikdown_rs.core.migrations import run_migrations


def run_or_exit(fn, *args, **kwargs):
    """Run fn; configuration/business/DB errors become `ERROR <msg>` + exit 1, no
    traceback (T-CLI-4, M7/T-CLI-8: never a raw traceback at the CLI boundary).
    Deliberately NOT bare Exception (M7): programming errors must still traceback."""
    try:
        return fn(*args, **kwargs)
    except (ConfigurationError, DownloadTimeoutError, sqlalchemy.exc.OperationalError) as exc:
        # M7: one clean line — for OperationalError the DBAPI `orig` message
        # ('database is locked') beats SQLAlchemy's multiline wrapper text.
        orig = getattr(exc, "orig", None)
        message = str(orig) if orig is not None else str(exc)
        typer.secho(f"ERROR {message}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1) from None


def raise_unimplemented(command: str) -> None:
    """Fail loudly for M0 skeleton commands (T-DATA-1 spirit): ERROR + exit 1."""
    typer.secho(
        f"ERROR: {command} is not implemented yet",
        err=True,
        fg=typer.colors.RED,
    )
    raise typer.Exit(1)


async def prepare_invocation(settings: Settings) -> Settings:
    """Migrations + fresh Settings per invocation (10.2, 5.4).

    T-ASYNC-4: Alembic's env.py calls asyncio.run() internally, so the migration
    itself must run via to_thread, never on the loop thread. `daemon healthcheck`
    and --version never call this: no migrations, no lock (5.4 exemption).
    """
    await asyncio.to_thread(run_migrations, settings.data_dir)
    return Settings()

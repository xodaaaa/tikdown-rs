"""Shared CLI helpers: error funneling and unimplemented-command stubs.

Trampas neutralizadas: T-CLI-4, T-CLI-5 (M0 loud-failure), T-CLI-1 (ASCII only).
Regla: 10.2, 10.1.
"""

import typer

from tikdown_rs.core.errors import ConfigurationError


def run_or_exit(fn, *args, **kwargs):
    """Run fn; ConfigurationError becomes `ERROR <msg>` + exit 1, no traceback (T-CLI-4)."""
    try:
        return fn(*args, **kwargs)
    except ConfigurationError as exc:
        typer.secho(f"ERROR {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1) from None


def raise_unimplemented(command: str) -> None:
    """Fail loudly for M0 skeleton commands (T-DATA-1 spirit): ERROR + exit 1."""
    typer.secho(
        f"ERROR: {command} is not implemented yet",
        err=True,
        fg=typer.colors.RED,
    )
    raise typer.Exit(1)

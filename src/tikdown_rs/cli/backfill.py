"""`tikdown-rs backfill` group: run, status, cancel, retry-failed (4 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help).
Regla: 10.1, 10.2.
"""

import typer

from tikdown_rs.cli.common import raise_unimplemented

app = typer.Typer(help="History backfill queue commands.")


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
    raise_unimplemented("backfill run")


@app.command()
def status(user: str = typer.Argument(..., help="Account handle, e.g. @user.")) -> None:
    """Show backfill progress for an account."""
    raise_unimplemented("backfill status")


@app.command()
def cancel(user: str = typer.Argument(..., help="Account handle, e.g. @user.")) -> None:
    """Cancel a running or queued backfill."""
    raise_unimplemented("backfill cancel")


@app.command()
def retry_failed(
    user: str | None = typer.Argument(
        None,
        help="Account handle, e.g. @user.",
    ),
    all: bool = typer.Option(False, "--all", help="Retry failed videos for all accounts."),
) -> None:
    """Retry failed videos for one account or all accounts with --all."""
    raise_unimplemented("backfill retry-failed")

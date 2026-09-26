"""`tikdown-rs accounts` group: add, list, pause, resume, notify, remove, check, stats (8).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help).
Regla: 10.1, 10.2. Deletion verb is `remove`, never `delete`.
"""

import typer

from tikdown_rs.cli.common import raise_unimplemented

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
    raise_unimplemented("accounts add")


@app.command()
def list() -> None:
    """List monitored accounts and their state."""
    raise_unimplemented("accounts list")


@app.command()
def pause(user: str = typer.Argument(..., help="Account handle, e.g. @user.")) -> None:
    """Pause monitoring for an account."""
    raise_unimplemented("accounts pause")


@app.command()
def resume(user: str = typer.Argument(..., help="Account handle, e.g. @user.")) -> None:
    """Resume monitoring for a paused account."""
    raise_unimplemented("accounts resume")


@app.command()
def notify(
    enabled: bool = typer.Option(
        ...,
        "--on/--off",
        help="Enable or disable notifications for all accounts.",
    ),
) -> None:
    """Toggle notifications on or off."""
    raise_unimplemented("accounts notify")


@app.command()
def remove(user: str = typer.Argument(..., help="Account handle, e.g. @user.")) -> None:
    """Remove an account (deletion verb is always `remove`)."""
    raise_unimplemented("accounts remove")


@app.command()
def check(user: str = typer.Argument(..., help="Account handle, e.g. @user.")) -> None:
    """Verify engine and cookies work for one account."""
    raise_unimplemented("accounts check")


@app.command()
def stats(user: str = typer.Argument(..., help="Account handle, e.g. @user.")) -> None:
    """Show per-account download statistics."""
    raise_unimplemented("accounts stats")

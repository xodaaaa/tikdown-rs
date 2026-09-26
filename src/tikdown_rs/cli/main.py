"""Typer CLI entry point.

Trampas neutralizadas: T-CLI-2. Regla: 10.2.
Decision: global callback with --version and invoke_without_command=True so
that bare `tikdown-rs --help` does not raise
"Could not get a command for this Typer instance".
"""

import typer

from tikdown_rs import __version__
from tikdown_rs.cli import (
    accounts,
    backfill,
    cookies,
    daemon,
    monitor,
    system,
    videos,
)

app = typer.Typer(help="TikDown-rs: self-hosted TikTok archive daemon.")

# The 7 noun groups of 10.1, registered 1:1 (T-CLI-5: the table is the spec).
app.add_typer(daemon.app, name="daemon")
app.add_typer(monitor.app, name="monitor")
app.add_typer(accounts.app, name="accounts")
app.add_typer(backfill.app, name="backfill")
app.add_typer(cookies.app, name="cookies")
app.add_typer(videos.app, name="videos")
app.add_typer(system.app, name="system")


@app.callback(invoke_without_command=True)
def main_callback(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the application version and exit.",
        is_eager=True,
    ),
) -> None:
    if version:
        typer.echo(f"tikdown-rs {__version__}")
        raise typer.Exit()

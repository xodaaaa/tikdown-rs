"""Typer CLI entry point.

Trampas neutralizadas: T-CLI-2. Regla: 10.2.
Decision: global callback with --version and invoke_without_command=True so
that bare `tikdown-rs --help` does not raise
"Could not get a command for this Typer instance".
"""

import typer

from tikdown_rs import __version__

app = typer.Typer(help="TikDown-rs: self-hosted TikTok archive daemon.")


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

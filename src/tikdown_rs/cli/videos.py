"""`tikdown-rs videos` group: last, export, integrity (3 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help).
Regla: 10.1, 10.2.
"""

import typer

from tikdown_rs.cli.common import raise_unimplemented

app = typer.Typer(help="Video archive queries and exports.")


@app.command()
def last(
    n: int | None = typer.Argument(None, help="Number of videos to show."),
) -> None:
    """Show the most recent archived videos."""
    raise_unimplemented("videos last")


@app.command()
def export(
    output_format: str = typer.Option(
        "json",
        "--format",
        help="Export format: json or csv.",
    ),
) -> None:
    """Export the video archive without markup or Rich wrapping (T-CLI-3)."""
    raise_unimplemented("videos export")


@app.command()
def integrity(
    username: str | None = typer.Argument(
        None,
        help="Restrict the check to one account handle.",
    ),
) -> None:
    """Check archive integrity for one account or the whole archive."""
    raise_unimplemented("videos integrity")

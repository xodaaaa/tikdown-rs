"""`tikdown-rs system` group: disk, backup, site render (3 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help).
Regla: 10.1, 10.2.
"""

import typer

from tikdown_rs.cli.common import raise_unimplemented

app = typer.Typer(help="System maintenance commands.")

site_app = typer.Typer(help="Static dashboard site commands.")
app.add_typer(site_app, name="site")


@app.command()
def disk(
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Resume downloads paused by the disk watermark.",
    ),
) -> None:
    """Show disk usage and the paused-by-disk queue state."""
    raise_unimplemented("system disk")


@app.command()
def backup() -> None:
    """Create a data backup."""
    raise_unimplemented("system backup")


@site_app.command()
def render() -> None:
    """Regenerate the static dashboard site (writes files, never serves HTTP)."""
    raise_unimplemented("system site render")

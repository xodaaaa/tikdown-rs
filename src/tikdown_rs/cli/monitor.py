"""`tikdown-rs monitor` group: start, stop (2 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help).
Regla: 10.1, 10.2.
"""

import typer

from tikdown_rs.cli.common import raise_unimplemented

app = typer.Typer(help="Monitor loop control commands.")


@app.command()
def start() -> None:
    """Start the monitor loop."""
    raise_unimplemented("monitor start")


@app.command()
def stop() -> None:
    """Stop the monitor loop."""
    raise_unimplemented("monitor stop")

"""`tikdown-rs daemon` group: run, stop, status, selfcheck, healthcheck (5 commands).

Trampas neutralizadas: T-CLI-5 (registered + --help smoke), T-CLI-1 (ASCII help).
Regla: 10.1, 10.2.
"""

import typer

from tikdown_rs.cli.common import raise_unimplemented

app = typer.Typer(help="Daemon lifecycle and supervision commands.")


@app.command()
def run() -> None:
    """Run the TikDown-rs daemon (container CMD)."""
    raise_unimplemented("daemon run")


@app.command()
def stop() -> None:
    """Request a graceful shutdown of the running daemon."""
    raise_unimplemented("daemon stop")


@app.command()
def status() -> None:
    """Show heartbeat, monitor state, selfcheck result and derived errors."""
    raise_unimplemented("daemon status")


@app.command()
def selfcheck() -> None:
    """Run a full self-check and record the result in daemon_state."""
    raise_unimplemented("daemon selfcheck")


@app.command()
def healthcheck() -> None:
    """Lightweight, network-free health probe for Docker HEALTHCHECK."""
    raise_unimplemented("daemon healthcheck")

"""Entrypoint de la CLI — cli/main.py (L-A1, §3).

@app.callback() global con --version e invoke_without_command=True (L-A1);
7 grupos de sustantivo registrados con add_typer (daemon, monitor, accounts,
backfill, cookies, videos, system). Sin comandos-verbo sueltos en la raíz.

story: e08s01
"""

from __future__ import annotations

import typer

from tikdown_rs import __version__

app = typer.Typer(
    name="tikdown-rs",
    help="CLI + Daemon + Telegram para archivar vídeos de TikTok",
    no_args_is_help=True,  # L-A1: ayuda sin argumentos
)


@app.callback(invoke_without_command=True)  # L-A1: callback global obligatorio
def _main(
    version: bool = typer.Option(False, "--version", help="Muestra la versión"),
) -> None:
    """Callback global (L-A1): --version no ejecuta migraciones (R10)."""
    if version:
        print(f"tikdown-rs {__version__}")
        raise typer.Exit()


def run() -> None:
    """Entrypoint del console script (L-A2: tikdown-rs = cli.main:run)."""
    app()


# Registrar los 7 grupos de sustantivo (§3)
from tikdown_rs.cli import (  # noqa: E402
    accounts,
    backfill,
    cookies,
    daemon,
    monitor,
    system,
    videos,
)

app.add_typer(daemon.app, name="daemon")
app.add_typer(monitor.app, name="monitor")
app.add_typer(accounts.app, name="accounts")
app.add_typer(backfill.app, name="backfill")
app.add_typer(cookies.app, name="cookies")
app.add_typer(system.app, name="system")
app.add_typer(videos.app, name="videos")

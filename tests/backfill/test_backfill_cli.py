"""e04s02 — cli/backfill.py: run/status orquestan services/backfill."""

# story: e04s02
from tikdown_rs.cli.backfill import app


def test_cli_grupo_backfill_comandos():
    """§3: el grupo backfill tiene run y status."""
    commands = {c.name for c in app.registered_commands}
    assert {"run", "status"}.issubset(commands)


def test_cli_backfill_solo_orquesta():
    """Regla de oro §3: cli/backfill no importa yt_dlp."""
    import inspect

    import tikdown_rs.cli.backfill as mod

    src = inspect.getsource(mod)
    assert "yt_dlp" not in src

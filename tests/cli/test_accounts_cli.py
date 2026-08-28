"""e03s01 — cli/accounts.py orquesta services/accounts (§3)."""

# story: e03s01
from tikdown_rs.cli.accounts import app


def test_cli_grupo_accounts_comandos():
    """§3: el grupo accounts tiene los subcomandos esperados."""
    commands = {c.name for c in app.registered_commands}
    expected = {"add", "list", "pause", "resume", "remove", "stats", "notify"}
    assert expected.issubset(commands), f"faltan: {expected - commands}"


def test_cli_grupo_solo_orquesta():
    """Regla de oro §3: cli/accounts no importa yt_dlp."""
    import inspect

    import tikdown_rs.cli.accounts as mod

    src = inspect.getsource(mod)
    assert "yt_dlp" not in src, "cli no debe importar yt_dlp"

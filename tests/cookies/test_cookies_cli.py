"""e05s02 — cli/cookies.py orquesta services/cookies (§3)."""
# story: e05s02
from tikdown_rs.cli.cookies import app


def test_cli_grupo_cookies_comandos():
    """§3: el grupo cookies tiene add/list/remove."""
    commands = {c.name for c in app.registered_commands}
    assert {"add", "list", "remove"}.issubset(commands)


def test_cli_cookies_solo_orquesta():
    """Regla de oro §3: cli/cookies no importa yt_dlp."""
    import inspect

    import tikdown_rs.cli.cookies as mod

    src = inspect.getsource(mod)
    assert "yt_dlp" not in src

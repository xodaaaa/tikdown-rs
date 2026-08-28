"""e07s02 — cli/system.py: system disk (§3)."""

# story: e07s02
from tikdown_rs.cli.system import app


def test_cli_grupo_system_disk():
    """§3: el grupo system tiene disk."""
    commands = {c.name for c in app.registered_commands}
    assert "disk" in commands


def test_cli_system_solo_orquesta():
    """Regla de oro §3: cli/system no importa yt_dlp."""
    import inspect

    import tikdown_rs.cli.system as mod

    src = inspect.getsource(mod)
    assert "yt_dlp" not in src

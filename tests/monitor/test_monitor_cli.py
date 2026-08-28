"""e03s02 — cli/monitor.py: start/stop escriben monitor_running."""

# story: e03s02
from tikdown_rs.cli.monitor import app


def test_cli_grupo_monitor_comandos():
    """§3: el grupo monitor tiene start y stop."""
    commands = {c.name for c in app.registered_commands}
    assert {"start", "stop"}.issubset(commands)


def test_cli_monitor_solo_orquesta():
    """Regla de oro §3: cli/monitor no importa yt_dlp."""
    import inspect

    import tikdown_rs.cli.monitor as mod

    src = inspect.getsource(mod)
    assert "yt_dlp" not in src

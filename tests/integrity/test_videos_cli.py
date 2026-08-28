"""e09s01 — cli/videos.py: integrity/last (§3)."""

# story: e09s01
from tikdown_rs.cli.videos import app


def test_cli_grupo_videos_comandos():
    """§3: el grupo videos tiene integrity y last."""
    commands = {c.name for c in app.registered_commands}
    assert {"integrity", "last"}.issubset(commands)


def test_cli_videos_solo_orquesta():
    """Regla de oro §3: cli/videos no importa yt_dlp."""
    import inspect

    import tikdown_rs.cli.videos as mod

    src = inspect.getsource(mod)
    assert "yt_dlp" not in src

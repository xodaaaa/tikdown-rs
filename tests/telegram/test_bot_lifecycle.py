"""e06s01 — ciclo de vida manual (T10), deps inyectadas (T26)."""

# story: e06s01
import inspect

from tikdown_rs.core.config import Settings
from tikdown_rs.daemon.telegram.bot import TelegramBot


def test_bot_no_usa_run_polling():
    """T10: el bot usa ciclo manual, nunca run_polling."""
    import tikdown_rs.daemon.telegram.bot as mod

    src = inspect.getsource(mod)
    # T10: nunca una LLAMADA a run_polling() (la mención en docstring es ok)
    assert "run_polling(" not in src, "nunca run_polling() (T10)"
    assert "start_polling(timeout=25)" in src, "usa updater.start_polling (T10)"


def test_bot_recibe_deps_inyectadas():
    """T26: el constructor recibe engine/motor/archive/clave (no los crea)."""
    sig = inspect.signature(TelegramBot.__init__)
    params = set(sig.parameters)
    assert "engine" in params
    assert "downloader" in params
    assert "archive" in params
    assert "fernet_key" in params
    assert "owns_engine" in params


def test_bot_modo_disabled_no_arranca():
    """Sin token o modo disabled → el bot no arranca."""
    settings = Settings(_env_file=None, telegram_bot_mode="disabled")
    assert settings.telegram_bot_token == ""

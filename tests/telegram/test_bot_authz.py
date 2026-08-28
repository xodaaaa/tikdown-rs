"""e06s01 — doble autorización (§6.3), F-18 (sin chat)."""
# story: e06s01
from tikdown_rs.core.config import Settings
from tikdown_rs.daemon.telegram.bot import is_authorized


def test_auth_chat_permitido():
    """§6.3: el chat permitido está autorizado."""
    settings = Settings(_env_file=None, telegram_chat_id="111")
    assert is_authorized(settings, chat_id="111", user_id=None) is True


def test_auth_chat_no_permitido():
    """§6.3: otro chat NO autorizado."""
    settings = Settings(_env_file=None, telegram_chat_id="111")
    assert is_authorized(settings, chat_id="222", user_id=None) is False


def test_auth_user_id_configurable():
    """§6.3: TELEGRAM_USER_ID (lista) autoriza desde el chat."""
    settings = Settings(
        _env_file=None,
        telegram_chat_id="111",
        telegram_user_id="333,444",
    )
    assert is_authorized(settings, chat_id="111", user_id="333") is True
    assert is_authorized(settings, chat_id="111", user_id="555") is False


def test_auth_sin_chat_f18():
    """F-18: update sin effective_chat → no autorizado (no revienta)."""
    settings = Settings(_env_file=None, telegram_chat_id="111")
    assert is_authorized(settings, chat_id=None, user_id=None) is False

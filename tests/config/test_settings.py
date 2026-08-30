"""e01s02 — Settings (pydantic-settings) con defaults y derivación de rutas."""

# story: e01s02
from pathlib import Path

import pytest

from tikdown_rs.core.config import ConfigurationError, Settings
from tikdown_rs.core.paths import default_outtmpl, videos_root


def test_settings_defaults():
    """Settings con defaults por defecto, sin depender del entorno real (T69)."""
    s = Settings(_env_file=None)
    assert s.data_dir == Path("/app/data")
    assert s.log_level == "INFO"
    assert s.heartbeat_interval_seconds == 10
    assert s.telegram_bot_mode in {"notifications", "commands", "both"}


def test_rutas_derivan_de_data_dir():
    """Toda ruta de datos deriva de data_dir (T8) — nunca del cwd. Usa Path (§14)."""
    s = Settings(_env_file=None, data_dir=Path("/tmp/tikdown-test"))
    assert videos_root(s) == Path("/tmp/tikdown-test/videos")
    outtmpl = Path("/tmp/tikdown-test/videos") / "%(uploader)s" / "%(id)s.%(ext)s"
    assert default_outtmpl(s) == outtmpl


def test_override_por_env(monkeypatch):
    """Override de un campo vía variable de entorno (12-factor)."""
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    s = Settings(_env_file=None)
    assert s.log_level == "DEBUG"


def test_validate_fail_sin_chat_id_commands():
    """Modo commands/both sin TELEGRAM_CHAT_ID → fail-fast (T25)."""
    s = Settings(_env_file=None, telegram_bot_mode="commands", telegram_chat_id="")
    with pytest.raises(ConfigurationError):
        s.validate_for_daemon()


def test_validate_fail_cooldown_max_menor_min():
    """Cooldown MAX < MIN → fail-fast (T25)."""
    s = Settings(
        _env_file=None,
        global_download_cooldown_min_seconds=120,
        global_download_cooldown_max_seconds=30,
    )
    with pytest.raises(ConfigurationError):
        s.validate_for_daemon()


def test_validate_ok_config_valida():
    """Configuración válida no lanza (sin notificaciones, cooldown correcto)."""
    s = Settings(_env_file=None)
    s.validate_for_daemon()  # no debe lanzar


# --- Auditoría 4.1: warning por variables de entorno desconocidas ---


def test_warn_unknown_tikdown_vars(monkeypatch, caplog):
    """4.1: TIKDOWN_/TELEGRAM_ sin match en Settings emiten warning (extra≠ignore).

    Convención del repo: mensaje-clave + datos en extra (r.var).
    """
    import logging

    monkeypatch.setenv("TIKDOWN_HEARTBEAT_INTTERVAL_SECONDS", "10")  # typo real
    with caplog.at_level(logging.WARNING, logger="tikdown_rs.config"):
        Settings(_env_file=None)
    vars_avisadas = [getattr(r, "var", "") for r in caplog.records]
    assert "TIKDOWN_HEARTBEAT_INTTERVAL_SECONDS" in vars_avisadas


def test_warn_unknown_telegram_vars(monkeypatch, caplog):
    """4.1: TELEGRAM_* desconocida también avisa (es el otro prefijo crítico)."""
    import logging

    monkeypatch.setenv("TELEGRAM_POLLING_TIMEOUT", "25")  # variable muerta histórica
    with caplog.at_level(logging.WARNING, logger="tikdown_rs.config"):
        Settings(_env_file=None)
    vars_avisadas = [getattr(r, "var", "") for r in caplog.records]
    assert "TELEGRAM_POLLING_TIMEOUT" in vars_avisadas


def test_no_warn_para_vars_conocidas(monkeypatch, caplog):
    """4.1: variables que SÍ matchean (o prefijos ajenos) no generan warning."""
    import logging

    monkeypatch.setenv("LOG_LEVEL", "DEBUG")  # conocida
    monkeypatch.setenv("HOME", "/tmp")  # prefijo ajeno, irrelevante
    monkeypatch.setenv("FERNET_KEY", "x")  # conocida fuera de pydantic (crypto.py)
    with caplog.at_level(logging.WARNING, logger="tikdown_rs.config"):
        s = Settings(_env_file=None)
    assert s.log_level == "DEBUG"
    assert not [r for r in caplog.records if getattr(r, "var", "") in {"LOG_LEVEL", "FERNET_KEY"}]


def test_warn_detecta_caso_del_docstring(monkeypatch, caplog):
    """4.1-r2: HEARTBEAT_INTTERVAL_SECONDS (el ejemplo del docstring) se detecta.

    Ronda 2 (2.2): los prefijos TIKDOWN_/TELEGRAM_ no cubrían ningún prefijo
    real de Settings (HEARTBEAT_, LOG_, DATA_, ...) — el propio ejemplo de la
    función no se detectaba. Ahora los prefijos se derivan de Settings.
    """
    import logging

    monkeypatch.setenv("HEARTBEAT_INTTERVAL_SECONDS", "10")
    with caplog.at_level(logging.WARNING, logger="tikdown_rs.config"):
        Settings(_env_file=None)
    assert "HEARTBEAT_INTTERVAL_SECONDS" in [getattr(r, "var", "") for r in caplog.records]


def test_warn_cubre_todos_los_prefijos_de_env_example():
    """4.1-r2: los prefijos vigilados cubren los de .env.example (los reales).

    Si un día se añade un campo con un prefijo nuevo, este test lo exige en
    los prefijos derivados de Settings — el warning nunca vuelve a quedarse
    ciego. Derivar de model_fields (2.2) hace que esto pase por construcción;
    el test protege contra una regresión a prefijos fijos incompletos.
    """
    import re

    from tikdown_rs.core.config import _owned_prefixes

    env_content = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    env_keys = [
        ln.split("=", 1)[0].strip()
        for ln in env_content.splitlines()
        if ln.strip() and not ln.strip().startswith("#") and "=" in ln
    ]
    required = {k.split("_")[0] + "_" for k in env_keys}
    derived = set(_owned_prefixes())
    assert required.issubset(derived), (
        f"prefijos de .env.example ausentes en los derivados de Settings: "
        f"{sorted(required - derived)}"
    )
    # Sanidad: los prefijos derivados tienen forma VALIDA_ (re.)
    assert all(re.fullmatch(r"[A-Z]+_", p) for p in _owned_prefixes())


def test_webda_variables_no_en_settings():
    """WEBDAV_* NO está en Settings (F-17) — lo lee el sidecar rclone, no la app."""
    s = Settings(_env_file=None)
    assert not hasattr(s, "webdav_user")
    assert not hasattr(s, "webdav_password")

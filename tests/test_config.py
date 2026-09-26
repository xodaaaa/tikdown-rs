"""Settings tests: env parity, fail-fast validation, unknown-var warning.

Trampas neutralizadas: T-DEPLOY-8, T-DATA-5, T-DEPLOY-9. Regla: 11.1, 13.1.
"""

import inspect
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from tikdown_rs.core import config as config_module
from tikdown_rs.core.config import Settings, warn_unknown_env
from tikdown_rs.core.errors import ConfigurationError

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"


@pytest.fixture(autouse=True)
def clean_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every Settings-related variable so tests inject their own (plan 13.1)."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def _env_example_keys() -> set[str]:
    keys: set[str] = set()
    for raw_line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def test_env_example_keys_match_settings_fields() -> None:
    """Bi-directional parity guard between .env.example and Settings (T-DEPLOY-9)."""
    file_keys = _env_example_keys()
    field_names = {name.upper() for name in Settings.model_fields}
    missing = field_names - file_keys
    undocumented = file_keys - field_names
    assert not missing, f"Settings fields missing from .env.example: {sorted(missing)}"
    assert not undocumented, (
        f".env.example variables without a Settings field: {sorted(undocumented)}"
    )


def test_cooldown_max_below_min_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLOBAL_DOWNLOAD_COOLDOWN_MAX_SECONDS", "5")
    monkeypatch.setenv("GLOBAL_DOWNLOAD_COOLDOWN_MIN_SECONDS", "30")
    with pytest.raises(ValidationError, match="(?i)cooldown"):
        Settings()


def test_zero_monitor_interval_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONITOR_INTERVAL_MINUTES", "0")
    with pytest.raises(ValidationError, match="(?i)monitor_interval_minutes"):
        Settings()


def test_invalid_log_file_when_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_FILE_WHEN", "weekly")
    with pytest.raises(ValidationError, match="(?i)log_file_when"):
        Settings()


def test_unwritable_data_dir_fails_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a regular file, not a directory")
    monkeypatch.setenv("DATA_DIR", str(blocker / "data"))
    settings = Settings()
    with pytest.raises(ConfigurationError, match="DATA_DIR"):
        settings.validate_for_daemon()


def test_bot_enabled_without_chat_id_fails_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = Settings()
    with pytest.raises(ConfigurationError, match="TELEGRAM_CHAT_ID"):
        settings.validate_for_daemon()


def test_unknown_env_var_warns_from_model_fields(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Prefixes must derive from model_fields: the uppercase env name may never
    # appear literally in the module source, or the derivation would be fake.
    source = inspect.getsource(config_module)
    assert "MONITOR_INTERVAL_MINUTES" not in source
    monkeypatch.setenv("MONITOR_INTERVL_MINUTES", "999")
    with caplog.at_level(logging.WARNING, logger="tikdown_rs.core.config"):
        warn_unknown_env()
    assert "MONITOR_INTERVL_MINUTES" in caplog.text


def test_cookie_validation_url_csv_split(monkeypatch: pytest.MonkeyPatch) -> None:
    """COOKIE_VALIDATION_URL is a comma-split list, never JSON (B.2.6, 7)."""
    monkeypatch.setenv("COOKIE_VALIDATION_URL", "url1,url2 ,url3")
    assert Settings().cookie_validation_url == ["url1", "url2", "url3"]


def test_cookie_validation_url_empty_is_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COOKIE_VALIDATION_URL", raising=False)
    assert Settings().cookie_validation_url == []


def test_minimal_env_builds_valid_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MONITOR_INTERVAL_MINUTES", "5")
    monkeypatch.setenv("MONITOR_AUTOSTART", "false")
    monkeypatch.setenv("GLOBAL_DOWNLOAD_COOLDOWN_MIN_SECONDS", "30")
    monkeypatch.setenv("GLOBAL_DOWNLOAD_COOLDOWN_MAX_SECONDS", "120")
    settings = Settings()
    assert settings.data_dir == tmp_path
    assert isinstance(settings.data_dir, Path)
    assert settings.monitor_interval_minutes == 5
    assert settings.monitor_autostart is False
    settings.validate_for_daemon()  # writable DATA_DIR passes

"""Application settings, environment-driven only (no dotenv parsing in code).

Trampas neutralizadas: T-DEPLOY-8, T-DATA-5, T-DEPLOY-9. Regla: §11.1.
"""

import logging
import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tikdown_rs.core.errors import ConfigurationError

logger = logging.getLogger(__name__)

_ALLOWED_LOG_FILE_WHEN = ("size", "midnight")


class Settings(BaseSettings):
    """Every tunable of the daemon, sourced from the environment (§11.1)."""

    model_config = SettingsConfigDict(extra="ignore")

    # Logging (§5.7)
    data_dir: Path = Path("/app/data")
    log_level: str = "INFO"
    log_file_path: str = ""
    log_file_max_bytes: int = Field(default=10_485_760, gt=0)
    log_file_backup_count: int = Field(default=7, ge=0)
    log_file_when: str = "size"

    # Telegram polling
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_user_id: str = ""
    polling_healthcheck_interval: int = Field(default=30, gt=0)
    polling_healthcheck_max_failures: int = Field(default=3, ge=1)

    # Monitor
    monitor_interval_minutes: int = Field(default=5, ge=1)
    monitor_autostart: bool = False

    # Download engine
    max_concurrent_downloads: int = Field(default=1, ge=1)
    global_download_cooldown_min_seconds: int = Field(default=30, gt=0)
    global_download_cooldown_max_seconds: int = Field(default=120, gt=0)
    download_timeout_seconds: int = Field(default=600, gt=0)
    download_format: str = ""
    # DR-17: YTDLP_ANTIBOT_BACKOFF_BASE/CEILING_SECONDS retired from config until
    # the retry epic (§17.2) exists (T-DEPLOY-9: no consumer, no variable).
    ytdlp_proxy_url: str = ""
    ytdlp_extractor_args: str = ""

    # Cookies
    cookie_validation_url: str = ""
    cookie_probe_max_entries: int = Field(default=5, ge=1)

    # Network monitor
    network_probe_url: str = ""
    network_probe_interval_seconds: int = Field(default=30, gt=0)
    network_probe_timeout_seconds: int = Field(default=5, gt=0)
    network_offline_threshold_consecutive_failures: int = Field(default=2, ge=1)

    # System health
    heartbeat_interval_seconds: int = Field(default=10, gt=0)
    disk_warning_free_percent: int = Field(default=10, ge=0, le=100)
    disk_check_interval_seconds: int = Field(default=900, gt=0)
    db_busy_timeout_alert_threshold: int = Field(default=20, ge=0)
    system_backup_retain_count: int = Field(default=7, ge=0)

    # Static site
    static_site_enabled: bool = False
    static_site_dir: str = ""
    static_site_interval_minutes: int = Field(default=15, ge=1)
    static_site_history_limit: int = Field(default=500, ge=1)

    @model_validator(mode="after")
    def _cooldown_max_not_below_min(self) -> "Settings":
        minimum = self.global_download_cooldown_min_seconds
        maximum = self.global_download_cooldown_max_seconds
        if maximum < minimum:
            raise ValueError(
                "GLOBAL_DOWNLOAD_COOLDOWN_MAX_SECONDS must be >= "
                f"GLOBAL_DOWNLOAD_COOLDOWN_MIN_SECONDS (cooldown max={maximum}, min={minimum})"
            )
        return self

    @field_validator("log_file_when")
    @classmethod
    def _log_file_when_allowed(cls, value: str) -> str:
        if value not in _ALLOWED_LOG_FILE_WHEN:
            allowed = " or ".join(repr(option) for option in _ALLOWED_LOG_FILE_WHEN)
            raise ValueError(f"LOG_FILE_WHEN must be {allowed}, got {value!r}")
        return value

    def validate_for_daemon(self) -> None:
        """Fail fast on configuration the field validators cannot see (§11.1)."""
        probe = self.data_dir / ".tikdown_write_probe"
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as exc:
            raise ConfigurationError(
                f"DATA_DIR is not creatable or writable: {self.data_dir} ({exc})"
            ) from exc
        if self.telegram_bot_token and not self.telegram_chat_id:
            raise ConfigurationError(
                "TELEGRAM_BOT_TOKEN is set but TELEGRAM_CHAT_ID is empty: "
                "set TELEGRAM_CHAT_ID to receive notifications, "
                "or unset TELEGRAM_BOT_TOKEN"
            )


def warn_unknown_env(env: Mapping[str, str] | None = None) -> None:
    """Warn about env variables that look like settings but name no field (T-DATA-5).

    Expected names and prefixes derive from ``Settings.model_fields``; a hardcoded
    list would go stale the moment a field is added.
    """
    if env is None:
        env = os.environ
    field_names = {name.upper() for name in Settings.model_fields}
    prefixes = {name.upper().split("_", 1)[0] + "_" for name in Settings.model_fields}
    for key in env:
        candidate = key.upper()
        if candidate in field_names:
            continue
        if any(candidate.startswith(prefix) for prefix in prefixes):
            logger.warning(
                "Unknown environment variable %s ignored: no Settings field matches it", key
            )


def load_settings() -> Settings:
    """Build Settings from the environment and warn about unknown variables."""
    settings = Settings()
    warn_unknown_env()
    return settings

"""Configuración de TikDown-rs (pydantic-settings).

Define `Settings` con todas las variables de entorno de §12 del plan maestro y
`validate_for_daemon()` — fail-fast de configuración al arrancar el daemon (T25).

story: e01s02
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

LOG = logging.getLogger("tikdown_rs.config")

# Prefijos que la app considera suyos: una variable con estos prefijos que no
# matchee Settings es casi seguro un typo o una variable muerta (auditoría 4.1).
_OWNED_PREFIXES = ("TIKDOWN_", "TELEGRAM_")


def warn_unknown_env_vars(env: dict[str, str] | None = None) -> list[str]:
    """Avisa de variables con prefijo propio que Settings no consume (4.1).

    `extra="ignore"` traga typos en silencio (p.ej. HEARTBEAT_INTTERVAL).
    Compara el entorno con los campos de Settings y loggea un WARNING por
    variable de prefijo TIKDOWN_/TELEGRAM_ sin correspondencia. Devuelve la
    lista de desconocidas (testable, sin capturar logs).
    """
    env = os.environ if env is None else env
    known = {f.upper() for f in Settings.model_fields}
    unknown = [key for key in env if key.startswith(_OWNED_PREFIXES) and key not in known]
    for key in unknown:
        LOG.warning(
            "config.unknown_env_var",
            extra={
                "var": key,
                "hint": "variable con prefijo propio que Settings no consume (typo o muerta)",
            },
        )
    return unknown


class ConfigurationError(Exception):
    """Configuración inválida detectada por validate_for_daemon()."""


class Settings(BaseSettings):
    """Configuración 12-factor derivada de variables de entorno / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",  # variables desconocidas del entorno no rompen la carga
    )

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002
        super().__init__(*args, **kwargs)
        # 4.1: el chequeo corre SIEMPRE al construir Settings (tests lo llaman
        # con _env_file=None; el entorno real procesal aquí).
        warn_unknown_env_vars()

    # Rutas — toda ruta de datos deriva de DATA_DIR (T8)
    data_dir: Path = Path("/app/data")

    # Logging
    log_level: str = "INFO"
    # e14s01: logs a archivo rotado (vacío = solo stdout)
    log_file_path: str = ""
    log_file_max_bytes: int = 10 * 1024 * 1024  # 10MB (rotación por tamaño)
    log_file_backup_count: int = 7  # retención
    log_file_when: str = "size"  # 'size' | 'midnight' (rotación temporal)

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_user_id: str = ""  # opcional; lista separada por comas de from_user.id autorizados
    telegram_bot_mode: str = "notifications"  # notifications | commands | both
    enable_external_notifications: bool = False

    # Monitor
    monitor_interval_minutes: int = 5
    monitor_autostart: bool = False  # el monitor siempre arranca detenido (§5.1)

    # Descargas
    max_concurrent_downloads: int = 1
    global_download_cooldown_min_seconds: int = 30  # [MIN, MAX] sorteo uniforme (T62)
    global_download_cooldown_max_seconds: int = 120
    ytdlp_antibot_backoff_base_seconds: int = 10
    ytdlp_antibot_backoff_ceiling_seconds: int = 120
    download_format: str = ""  # override opcional del formato de §4.2

    # DB / disco
    db_busy_timeout_alert_threshold: int = 20
    disk_warning_free_percent: int = 10

    # Daemon / heartbeat
    heartbeat_interval_seconds: int = 10  # frescura = 3x este valor (T50)

    # Backups
    system_backup_retain_count: int = 7

    # Reintentos
    max_video_retry_count: int = 5
    max_video_total_time_seconds: int = 900

    # Cookies / yt-dlp
    cookie_validation_url: str = ""  # sonda(s) de validación de cookies (§7)
    ytdlp_proxy_url: str = ""  # opcional; lista separada por comas (§4.7)
    ytdlp_extractor_args: str = ""  # passthrough a extractor-args (§12)

    # Red
    network_probe_url: str = ""  # endpoints neutrales, separados por coma; nunca TikTok
    network_probe_interval_seconds: int = 30
    network_probe_timeout_seconds: int = 5  # cableado desde Settings (F-13)
    network_offline_threshold_consecutive_failures: int = 2

    def validate_for_daemon(self) -> None:
        """Fail-fast de configuración antes de crear recursos (T25).

        Lanza ConfigurationError si la configuración no permite un arranque
        coherente del daemon.
        """
        if self.telegram_bot_mode in {"commands", "both"} and not self.telegram_chat_id:
            raise ConfigurationError(
                f"TELEGRAM_BOT_MODE={self.telegram_bot_mode} requiere TELEGRAM_CHAT_ID"
            )
        if self.enable_external_notifications and not self.telegram_bot_token:
            raise ConfigurationError(
                "ENABLE_EXTERNAL_NOTIFICATIONS=true requiere TELEGRAM_BOT_TOKEN"
            )
        if self.global_download_cooldown_max_seconds < self.global_download_cooldown_min_seconds:
            raise ConfigurationError(
                "GLOBAL_DOWNLOAD_COOLDOWN_MAX_SECONDS < GLOBAL_DOWNLOAD_COOLDOWN_MIN_SECONDS (T25)"
            )
        if self.heartbeat_interval_seconds <= 0:
            raise ConfigurationError(
                f"HEARTBEAT_INTERVAL_SECONDS debe ser > 0 (recibido "
                f"{self.heartbeat_interval_seconds})"
            )

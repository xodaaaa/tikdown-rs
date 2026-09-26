"""JSON logging to stdout always, optional rotated file with the same formatter.

Trampas neutralizadas: T-DEPLOY-6 (reaplicable), T-DATA-1 (nada de WARNING mudos:
el formatter serializa exc_info). Regla: §5.7, §2.1.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

from tikdown_rs.core.config import Settings

_OWNED_ATTRIBUTE = "_tikdown_rs_owned"


class JsonFormatter(logging.Formatter):
    """Serialize each record as one JSON line (§2.1: stdlib only, no structlog)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(settings: Settings, force: bool = False) -> None:
    """Attach the stdout JSON handler plus the optional rotated file handler.

    Reapplication-safe: replaces handlers attached by a previous call, so a later
    work unit can reapply with ``force=True`` after Alembic's fileConfig stomps
    the root logger (T-DEPLOY-6). With ``force=False`` foreign handlers survive.
    """
    root = logging.getLogger()
    if force:
        stale = list(root.handlers)
    else:
        stale = [h for h in root.handlers if getattr(h, _OWNED_ATTRIBUTE, False)]
    for handler in stale:
        root.removeHandler(handler)
        handler.close()

    formatter = JsonFormatter()
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler._tikdown_rs_owned = True  # type: ignore[attr-defined]
    root.addHandler(stdout_handler)

    if not settings.log_file_path:
        root.setLevel(settings.log_level.upper())
        return

    if settings.log_file_when == "midnight":
        file_handler: logging.Handler = TimedRotatingFileHandler(
            settings.log_file_path,
            when="midnight",
            backupCount=settings.log_file_backup_count,
            encoding="utf-8",
        )
    else:
        file_handler = RotatingFileHandler(
            settings.log_file_path,
            maxBytes=settings.log_file_max_bytes,
            backupCount=settings.log_file_backup_count,
            encoding="utf-8",
        )
    file_handler.setFormatter(formatter)
    file_handler._tikdown_rs_owned = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)
    root.setLevel(settings.log_level.upper())

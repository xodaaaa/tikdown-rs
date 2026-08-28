"""Logging estructurado — logging stdlib con formatter JSON ad-hoc.

Decisión F-20: nunca structlog. JSON a stdout en el daemon; consola legible
en CLI. Nivel vía LOG_LEVEL. Reaplicable tras migración Alembic (T72) con
`basicConfig(force=True)`.

story: e01s03
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

# Logger raíz del proyecto: `tikdown_rs.*`
LOG = logging.getLogger("tikdown_rs")


class JsonFormatter(logging.Formatter):
    """Formatter que emite cada registro como una línea JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(
    level: str,
    json_output: bool = False,
    log_file_path: str = "",
    log_file_max_bytes: int = 10 * 1024 * 1024,
    log_file_backup_count: int = 7,
    log_file_when: str = "size",
) -> None:
    """Configura el root logger: JSON a stdout (daemon) o consola legible (CLI).

    e14s01: si log_file_path está configurado, añade un handler de archivo
    ROTADO (RotatingFileHandler por tamaño o TimedRotatingFileHandler por
    tiempo) con el MISMO JsonFormatter — JSON a stdout + archivo.

    Usa basicConfig(force=True) para que pueda reaplicarse tras la migración
    Alembic, que pisa el root logger con fileConfig (T72).
    """
    from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    handlers: list[logging.Handler] = []

    # stdout (siempre) — JSON en daemon, consola legible en CLI
    stdout_handler = logging.StreamHandler(sys.stdout)
    if json_output:
        stdout_handler.setFormatter(JsonFormatter())
    else:
        stdout_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handlers.append(stdout_handler)

    # e14s01: archivo rotado (opcional) — JSON consistente con stdout
    if log_file_path:
        path = Path(log_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if log_file_when == "midnight":
            file_handler = TimedRotatingFileHandler(
                str(path), when="midnight", backupCount=log_file_backup_count
            )
        else:
            file_handler = RotatingFileHandler(
                str(path),
                maxBytes=log_file_max_bytes,
                backupCount=log_file_backup_count,
            )
        file_handler.setFormatter(JsonFormatter())
        handlers.append(file_handler)

    logging.basicConfig(level=numeric_level, handlers=handlers, force=True)

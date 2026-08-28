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


def setup_logging(level: str, json_output: bool = False) -> None:
    """Configura el root logger: JSON a stdout (daemon) o consola legible (CLI).

    Usa basicConfig(force=True) para que pueda reaplicarse tras la migración
    Alembic, que pisa el root logger con fileConfig (T72).
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    handler: logging.Handler
    if json_output:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    logging.basicConfig(level=numeric_level, handlers=[handler], force=True)

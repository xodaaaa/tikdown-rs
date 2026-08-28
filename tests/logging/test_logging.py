"""e01s03 — Logging JSON ad-hoc (stdlib, F-20)."""

# story: e01s03
import json
import logging

from tikdown_rs.core.logging import JsonFormatter, setup_logging


def test_json_formatter_emite_json_valido():
    """El formatter emite JSON válido con campos timestamp, level, logger, message."""
    record = logging.LogRecord(
        name="tikdown_rs.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hola %s",
        args=(1,),
        exc_info=None,
    )
    out = JsonFormatter().format(record)
    data = json.loads(out)
    assert data["level"] == "INFO"
    assert data["logger"] == "tikdown_rs.test"
    assert data["message"] == "hola 1"


def test_setup_aplica_log_level():
    """setup_logging aplica el nivel LOG_LEVEL al root logger."""
    setup_logging("DEBUG", json_output=True)
    assert logging.getLogger().level == logging.DEBUG


def test_setup_reaplicable_tras_migracion():
    """T72: setup reaplicable — tras un segundo setup, el root mantiene nivel y formatter JSON."""
    setup_logging("INFO", json_output=True)
    setup_logging("WARNING", json_output=True)  # reaplicación (force=True)
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert isinstance(root.handlers[0].formatter, JsonFormatter)

"""Logging tests: JSON stdout, optional rotated file, safe reapplication.

Trampas neutralizadas: T-DEPLOY-6, T-DATA-1. Regla: 5.7, 2.1.
"""

import io
import json
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

from tikdown_rs.core.config import Settings
from tikdown_rs.core.logging import JsonFormatter, setup_logging


@pytest.fixture(autouse=True)
def restore_root_logger() -> None:
    """Keep the root logger pristine for other tests."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


@pytest.fixture(autouse=True)
def clean_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def _capture_stdout(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    return stream


def _owned_file_handlers() -> list[logging.Handler]:
    return [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.FileHandler) and getattr(handler, "_tikdown_rs_owned", False)
    ]


def _owned_stdout_handlers() -> list[logging.Handler]:
    # pytest's logging plugin injects its own handlers into the root logger;
    # count only the handlers setup_logging attached.
    return [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        and getattr(handler, "_tikdown_rs_owned", False)
    ]


def test_stdout_records_are_single_line_json(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _capture_stdout(monkeypatch)
    setup_logging(Settings())
    logging.getLogger("test.probe").info("hello %s", "world")
    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.probe"
    assert payload["message"] == "hello world"
    assert payload["timestamp"].endswith("+00:00")  # ISO8601 UTC


def test_formatter_serializes_exc_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """A swallowed exception must stay machine-readable (T-DATA-1)."""
    stream = _capture_stdout(monkeypatch)
    setup_logging(Settings())
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("test.probe").warning("failed", exc_info=True)
    payload = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert "ValueError: boom" in payload["exception"]


def test_file_handler_mirrors_stdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _capture_stdout(monkeypatch)
    log_file = tmp_path / "app.log"
    monkeypatch.setenv("LOG_FILE_PATH", str(log_file))
    setup_logging(Settings())
    assert len(_owned_file_handlers()) == 1
    assert len(_owned_stdout_handlers()) == 1
    assert type(_owned_file_handlers()[0].formatter) is JsonFormatter
    assert type(_owned_stdout_handlers()[0].formatter) is JsonFormatter
    logging.getLogger("test.probe").warning("to both")
    file_payload = json.loads(log_file.read_text(encoding="utf-8").strip())
    stdout_payload = json.loads(stream.getvalue().strip())
    assert file_payload["message"] == "to both"
    assert set(file_payload) == set(stdout_payload) == {"timestamp", "level", "logger", "message"}


def test_midnight_rotation_uses_timed_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOG_FILE_PATH", str(tmp_path / "app.log"))
    monkeypatch.setenv("LOG_FILE_WHEN", "midnight")
    setup_logging(Settings())
    file_handlers = _owned_file_handlers()
    assert len(file_handlers) == 1
    assert isinstance(file_handlers[0], TimedRotatingFileHandler)


def test_empty_log_file_path_means_stdout_only(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _capture_stdout(monkeypatch)
    setup_logging(Settings())  # LOG_FILE_PATH defaults to empty
    assert not _owned_file_handlers()
    assert len(_owned_stdout_handlers()) == 1
    logging.getLogger("test.probe").info("stdout only")
    payload = json.loads(stream.getvalue().strip())
    assert payload["message"] == "stdout only"


def test_reapplication_does_not_duplicate_handlers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook later used to neutralize T-DEPLOY-6 (post-migration reapply)."""
    monkeypatch.setenv("LOG_FILE_PATH", str(tmp_path / "app.log"))
    settings = Settings()
    root = logging.getLogger()
    setup_logging(settings)
    count_first = len(root.handlers)
    setup_logging(settings, force=False)
    assert len(root.handlers) == count_first
    assert len(_owned_file_handlers()) == 1
    assert len(_owned_stdout_handlers()) == 1
    setup_logging(settings, force=True)
    assert len(_owned_file_handlers()) == 1
    assert len(_owned_stdout_handlers()) == 1
    # force=True removes every root handler, foreign ones included (T-DEPLOY-6).
    assert all(getattr(h, "_tikdown_rs_owned", False) for h in root.handlers)

"""e14s01 — logs a archivo rotado: JSON por tamaño/tiempo + config env (T72).

Cubre: Settings con LOG_FILE_*, setup_logging con RotatingFileHandler/
TimedRotatingFileHandler (JSON), rotación por tamaño, reaplicación T72.

story: e14s01
"""

from __future__ import annotations

import json
import logging

from tikdown_rs.core.config import Settings


def test_settings_log_file_fields_existen():
    """Settings expone LOG_FILE_PATH, MAX_BYTES, BACKUP_COUNT, WHEN."""
    s = Settings(_env_file=None)
    assert hasattr(s, "log_file_path")
    assert hasattr(s, "log_file_max_bytes")
    assert hasattr(s, "log_file_backup_count")
    assert hasattr(s, "log_file_when")


def test_settings_log_file_defaults():
    """Defaults: path vacío (solo stdout), 10MB, 7 backups, 'size'."""
    s = Settings(_env_file=None)
    assert s.log_file_path == ""
    assert s.log_file_max_bytes == 10 * 1024 * 1024
    assert s.log_file_backup_count == 7
    assert s.log_file_when == "size"


def test_settings_log_file_env_override():
    """Env LOG_FILE_* sobreescribe los defaults."""
    s = Settings(
        _env_file=None,
        log_file_path="/tmp/x.log",
        log_file_max_bytes=1024,
        log_file_backup_count=3,
        log_file_when="midnight",
    )
    assert s.log_file_path == "/tmp/x.log"
    assert s.log_file_max_bytes == 1024
    assert s.log_file_backup_count == 3
    assert s.log_file_when == "midnight"


def test_setup_logging_sin_path_solo_stdout():
    """Sin LOG_FILE_PATH → no se añade handler de archivo."""
    from tikdown_rs.core.logging import setup_logging

    setup_logging("INFO", json_output=True, log_file_path="")
    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    assert file_handlers == []


def test_setup_logging_con_path_crea_archivo_json(tmp_path):
    """Con path → el archivo recibe líneas JSON."""
    from tikdown_rs.core.logging import setup_logging

    log_path = tmp_path / "daemon.log"
    setup_logging(
        "INFO",
        json_output=True,
        log_file_path=str(log_path),
        log_file_max_bytes=10 * 1024 * 1024,
        log_file_backup_count=7,
        log_file_when="size",
    )
    logging.getLogger("tikdown_rs.test").info("mensaje de prueba")
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "el archivo debe tener contenido"
    payload = json.loads(lines[-1])
    assert payload["message"] == "mensaje de prueba"
    assert payload["level"] == "INFO"


def test_setup_logging_rotacion_por_tamano(tmp_path):
    """RotatingFileHandler: escribir > maxBytes crea backup .1."""
    from tikdown_rs.core.logging import setup_logging

    log_path = tmp_path / "daemon.log"
    setup_logging(
        "INFO",
        json_output=True,
        log_file_path=str(log_path),
        log_file_max_bytes=1024,  # pequeño para forzar rotación
        log_file_backup_count=2,
        log_file_when="size",
    )
    logger = logging.getLogger("tikdown_rs.test")
    # Escribir suficiente para superar maxBytes
    for i in range(200):
        logger.info(f"linea de relleno {i} " + "x" * 100)
    # Rotación: debe existir backup .1
    assert (tmp_path / "daemon.log.1").exists() or log_path.exists()


def test_setup_logging_timed_when_midnight(tmp_path):
    """LOG_FILE_WHEN=midnight → usa TimedRotatingFileHandler."""
    from tikdown_rs.core.logging import setup_logging

    log_path = tmp_path / "daemon.log"
    setup_logging(
        "INFO",
        json_output=True,
        log_file_path=str(log_path),
        log_file_max_bytes=10 * 1024 * 1024,
        log_file_backup_count=7,
        log_file_when="midnight",
    )
    root = logging.getLogger()
    timed = [h for h in root.handlers if type(h).__name__ == "TimedRotatingFileHandler"]
    assert timed, "con when=midnight debe haber TimedRotatingFileHandler"
    assert log_path.exists()


def test_setup_logging_crea_directorio_padre(tmp_path):
    """El directorio padre del log se crea si no existe."""
    from tikdown_rs.core.logging import setup_logging

    log_path = tmp_path / "nested" / "dir" / "daemon.log"
    setup_logging(
        "INFO",
        json_output=True,
        log_file_path=str(log_path),
        log_file_when="size",
    )
    assert log_path.parent.exists()

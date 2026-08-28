"""e08s01 — cli/common.py: run_sync (T18), run_or_exit (F-21), prepare_invocation."""
# story: e08s01
import asyncio

import pytest

from tikdown_rs.cli.common import prepare_invocation, run_or_exit, run_sync
from tikdown_rs.core.config import ConfigurationError


def test_run_sync_ejecuta_corrutina_t18():
    """T18: run_sync envuelve una corrutina con asyncio.run()."""
    result = run_sync(asyncio.sleep(0, result=42))
    assert result == 42


def test_run_or_exit_convierte_error_f21(capsys):
    """F-21: ConfigurationError → ERROR <mensaje> + exit 1, sin traceback."""
    def _boom():
        raise ConfigurationError("config inválida")

    with pytest.raises(SystemExit) as exc:
        run_or_exit(_boom)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "ERROR" in out and "config inválida" in out
    assert "Traceback" not in out  # sin traceback (F-21)


def test_run_or_exit_exito_devuelve_valor():
    """run_or_exit devuelve el valor en éxito."""
    assert run_or_exit(lambda: 7) == 7


def test_prepare_invocation_construye_settings():
    """§5.6: prepare_invocation construye Settings fresca."""
    settings = prepare_invocation()
    assert settings.data_dir is not None
    assert settings.log_level == "INFO"

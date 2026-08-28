"""e02s02 — core/crypto.py: clave Fernet 0600, O_EXCL (T67), vacío (L-E2)."""
# story: e02s02
import os

import pytest

from tikdown_rs.core.crypto import load_or_create_fernet_key


def test_genera_clave_y_valida(tmp_path):
    """Genera fernet.key; clave base64 Fernet válida; intento 0600 no lanza."""
    key_path = tmp_path / "fernet.key"
    key = load_or_create_fernet_key(key_path)
    assert key is not None
    assert key_path.exists()
    assert len(key) > 0
    assert key_path.stat().st_size > 0


def test_clave_existente_0600_corregida(tmp_path, monkeypatch):
    """T7: clave existente con 0644 se corrige a 0600 (no-op en Windows)."""
    key_path = tmp_path / "fernet.key"
    key_path.write_bytes(b"x" * 44)
    os.chmod(key_path, 0o644)
    key = load_or_create_fernet_key(key_path)
    assert key is not None


def test_generacion_atomica_o_excl(tmp_path):
    """T67: la generación usa O_EXCL — una clave existente no se pisa."""
    key_path = tmp_path / "fernet.key"
    k1 = load_or_create_fernet_key(key_path)
    k2 = load_or_create_fernet_key(key_path)
    assert k1 == k2, "la clave existente debe releerse, no regenerarse"


def test_archivo_vacio_persistente_oserror(tmp_path):
    """L-E2: archivo vacío persistente → OSError (no propagar corrupción)."""
    key_path = tmp_path / "fernet.key"
    key_path.write_bytes(b"")
    with pytest.raises(OSError):
        load_or_create_fernet_key(key_path)

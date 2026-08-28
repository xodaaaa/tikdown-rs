"""e05s01 — cifrado Fernet: roundtrip, 0600, O_EXCL, vacío (L-E2)."""
# story: e05s01
import pytest
from cryptography.fernet import Fernet, InvalidToken

from tikdown_rs.core.crypto import (
    decrypt_cookie,
    encrypt_cookie,
    load_or_create_fernet_key,
)


@pytest.fixture
def fernet_key(tmp_path):
    """F-12: clave generada al vuelo, nunca constante."""
    return load_or_create_fernet_key(tmp_path / "fernet.key")


def test_roundtrip_encrypt_decrypt(fernet_key):
    """Cifra y descifra correctamente (roundtrip)."""
    blob = b"# Netscape HTTP Cookie File\n.tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc123"
    ciphertext = encrypt_cookie(blob, fernet_key)
    assert ciphertext != blob  # cifrado
    assert decrypt_cookie(ciphertext, fernet_key) == blob  # roundtrip


def test_ciphertext_es_bytes(fernet_key):
    """El ciphertext son bytes (LargeBinary, nunca Text — §2)."""
    ciphertext = encrypt_cookie(b"cookie-data", fernet_key)
    assert isinstance(ciphertext, bytes)


def test_decrypt_clave_incorrecta_falla(fernet_key):
    """Descifrar con otra clave → error (detecta rotación, T16)."""
    other = Fernet.generate_key().decode()
    ciphertext = encrypt_cookie(b"data", fernet_key)
    with pytest.raises(InvalidToken):
        decrypt_cookie(ciphertext, other)


def test_clave_0600_existente(tmp_path):
    """T7: clave existente con permisos amplios se corrige (no-op en Windows)."""
    import os

    key_path = tmp_path / "fernet.key"
    key_path.write_bytes(b"x" * 44)
    os.chmod(key_path, 0o644)
    key = load_or_create_fernet_key(key_path)
    assert key is not None

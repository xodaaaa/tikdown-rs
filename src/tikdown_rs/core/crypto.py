"""Cifrado Fernet — core/crypto.py.

Clave con permisos 0600 (T7), generación atómica con O_EXCL (T67), tolerancia
a archivo vacío en la ventana de creación (L-E2). Jerarquía: FERNET_KEY env →
DATA_DIR/fernet.key → generar.

story: e02s02
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path

from cryptography.fernet import Fernet

LOG = logging.getLogger("tikdown_rs.crypto")


def _read_key_with_retry(key_path: Path, attempts: int = 50, delay: float = 0.01) -> str:
    """Lee la clave; tolera archivo vacío en la ventana de creación (L-E2).

    Entre el O_EXCL del creador y su escritura, el archivo puede leerse vacío.
    Reintenta la lectura; solo propaga corrupción no vacía o vacío persistente.
    """
    for _ in range(attempts):
        data = key_path.read_bytes()
        if data:
            return data.decode("utf-8").strip()
        time.sleep(delay)
    raise OSError(f"fernet.key vacío de forma persistente tras {attempts} intentos (L-E2)")


def _chmod_600(key_path: Path) -> None:
    """Aplica 0600; en Windows no aplica POSIX perms (no-op)."""
    try:
        os.chmod(key_path, 0o600)
    except OSError:  # pragma: no cover - Windows
        LOG.warning("crypto.chmod_0600_noop", extra={"path": str(key_path)})


def load_or_create_fernet_key(key_path: Path, env_key: str | None = None) -> str:
    """Carga o genera la clave Fernet. Devuelve la clave (str base64).

    - Si FERNET_KEY env está presente, la usa.
    - Si el archivo existe, lo lee (con retry si vacío, L-E2) y corrige 0600 (T7).
    - Si no existe, lo genera con O_EXCL (T67): el perdedor relee la existente.
    """
    if env_key:
        return env_key

    if key_path.exists():
        key = _read_key_with_retry(key_path)
        _chmod_600(key_path)  # T7: corregir permisos sobre clave existente
        return key

    # Generación atómica (T67): open('xb') = O_EXCL — falla si otro proceso ganó.
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # El otro proceso ganó la carrera — releer la existente (T67).
        return _read_key_with_retry(key_path)

    try:
        key = Fernet.generate_key().decode("utf-8")
        with os.fdopen(fd, "w") as f:
            f.write(key)
            f.flush()
            os.fsync(f.fileno())
        _chmod_600(key_path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(key_path)
        raise  # re-lanzar la excepción original (generación fallida)
    return key


def encrypt_cookie(blob: bytes, key: str) -> bytes:
    """Cifra un blob de cookies con Fernet (para encrypted_blob, §2)."""
    return Fernet(key.encode()).encrypt(blob)


def decrypt_cookie(ciphertext: bytes, key: str) -> bytes:
    """Descifra un blob de cookies (encrypted_blob, LargeBinary)."""
    return Fernet(key.encode()).decrypt(ciphertext)

"""Servicio de cookies — services/cookies.py (§7).

Importación (cifrado Fernet, best-effort T14/F-15), validación triestado
(valid/invalid/inconclusive, F-16), sonda robusta (T57/T74/R12),
get_working_cookie (L-E3, sesiones cortas T32).

story: e05s02
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tikdown_rs.core.crypto import decrypt_cookie, encrypt_cookie
from tikdown_rs.core.download_engine import classify_failure
from tikdown_rs.models.models import Cookie

LOG = logging.getLogger("tikdown_rs.cookies")

# T74/L-E4: la sonda itera las primeras 5 entradas buscando formatos de vídeo
PROBE_MAX_ENTRIES = 5
# T33: clamp de expiraciones absurdas a año 2100
_YEAR_2100 = 4102444800  # 2100-01-01T00:00:00Z


def clamp_expiration(timestamp: int) -> int:
    """Clampa una expiración absurda a año 2100 (T33, evita OverflowError)."""
    return min(timestamp, _YEAR_2100)


async def add(
    session: AsyncSession,
    source_path,
    fernet_key: str,
    keep_source: bool = False,
    label: str | None = None,
) -> Cookie:
    """Importa cookies.txt/.json, cifra con Fernet y guarda (F-15/T14/T73)."""
    blob = source_path.read_bytes()
    ciphertext = encrypt_cookie(blob, fernet_key)
    cookie = Cookie(
        label=label or source_path.name,
        encrypted_blob=ciphertext,
    )
    session.add(cookie)
    await session.commit()

    # Borrado best-effort (T14): fallo no rompe el éxito; --keep-source conserva (F-15)
    if not keep_source:
        try:
            source_path.unlink()
        except OSError as exc:
            LOG.warning("cookies.import_keep_source_failed", extra={"exc": repr(exc)})
    return cookie


def validate_cookie_result(raw_result: str) -> str:
    """Clasifica un resultado de validación: valid | invalid | inconclusive (§7).

    Solo un fallo de AUTH CONFIRMADO → invalid; red/timeout/extractor → inconclusive.
    """
    if raw_result == "ok":
        return "valid"
    category = classify_failure(raw_result)
    if category == "definitive":
        return "invalid"
    return "inconclusive"


def probe_finds_video(entries: list[dict]) -> bool:
    """¿La sonda encontró un formato de vídeo en las primeras 5 entradas? (T74/L-E4)"""
    for entry in entries[:PROBE_MAX_ENTRIES]:
        formats = entry.get("formats") or []
        if any(f.get("ext") in ("mp4", "m4a", "webm") for f in formats):
            return True
    return False


async def get_working_cookie(
    session: AsyncSession,
    validate_fn,
    fernet_key: str,
) -> Cookie | None:
    """Devuelve una cookie working (L-E3/§7/T32).

    - Cookies valid ordenadas por last_validated_at desc.
    - SOLO rechaza ante 'invalid' (L-E3: inconclusive conserva con log).
    - Sesiones cortas (T32): el llamador lee el blob y valida fuera de la sesión.
    """
    result = await session.execute(
        select(Cookie)
        .where(Cookie.validation_state == "valid")
        .order_by(Cookie.last_validated_at.desc())
    )
    candidates = list(result.scalars().all())
    for cookie in candidates:
        blob = decrypt_cookie(cookie.encrypted_blob, fernet_key)
        outcome = validate_fn(blob)
        if outcome == "invalid":
            continue  # L-E3: solo invalid rechaza
        if outcome == "inconclusive":
            LOG.info("cookies.working_inconclusive_conservada", extra={"id": cookie.id})
        return cookie
    return None

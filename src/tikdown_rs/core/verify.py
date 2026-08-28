"""Selfcheck del daemon — core/verify.py (§4.1).

Verifica impersonación TLS (curl-cffi, T6), ffmpeg/ffprobe (T46) y crypto
(clave + descifrar cookie, T16). Versión yt-dlp contra yt_dlp.version
(T4). El daemon no arranca si la impersonación no está operativa.

story: e02s02
"""

from __future__ import annotations

import logging
import shutil
import subprocess

import yt_dlp

LOG = logging.getLogger("tikdown_rs.verify")


def selfcheck_impersonation() -> bool:
    """Verifica que la impersonación TLS está operativa (§4.1, T6).

    Distingue 3 causas de fallo (T6): curl-cffi ausente, versión no soportada,
    targets vacíos pese a librería correcta. Los targets son OBJETOS
    ImpersonateTarget (L-D1). Sin targets → SystemExit(1).
    """
    ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
    available: list = []
    try:
        raw = getattr(ydl, "_get_available_impersonate_targets", lambda: [])()
        # Forma de retorno variable entre versiones (lista de targets o de
        # tuplas (target, engine)); normalizar defensivamente.
        for item in raw:
            if isinstance(item, tuple) and item:
                available.append(item[0])  # ImpersonateTarget en [0]
            else:
                available.append(item)
    except Exception:
        LOG.warning("selfcheck.impersonation_api_changed", exc_info=True)

    if not available:
        try:
            import curl_cffi  # noqa: F401
        except ImportError:
            msg = (
                "curl-cffi ausente. Instalar yt-dlp[default,curl-cffi] (T6 causa 1)."
            )
        else:
            msg = (
                "Impersonación TLS no disponible: targets vacíos pese a curl-cffi "
                "presente (T6 causa 3). Verificar --list-impersonate-targets; "
                "posible limitación de plataforma (ARM64) o cambio de API interna."
            )
        LOG.critical("selfcheck.impersonation_failed: %s", msg)
        raise SystemExit(1)

    LOG.info(
        "selfcheck.impersonation_ok",
        extra={"targets": len(available)},
    )
    return True


def selfcheck_ffmpeg() -> bool:
    """Verifica que ffmpeg/ffprobe son ejecutables (T46, dependencia dura)."""
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        LOG.critical(
            "selfcheck.ffmpeg_missing: %s (T46 — dependencia dura)", ", ".join(missing)
        )
        raise SystemExit(1)
    LOG.info("selfcheck.ffmpeg_ok")
    return True


def ytdlp_version_internal() -> str:
    """Versión interna de yt-dlp (T4) — coincide con el tag de GitHub, no el gestor."""
    from yt_dlp.version import __version__

    return __version__


def _ffprobe_version() -> str | None:
    """Versión de ffprobe (para el reporte)."""
    path = shutil.which("ffprobe")
    if not path:
        return None
    try:
        out = subprocess.run(
            [path, "-version"], capture_output=True, text=True, timeout=5
        )
        return out.stdout.splitlines()[0] if out.stdout else None
    except Exception:  # pragma: no cover
        return None


def selfcheck_crypto(fernet_key: str, db_path=None) -> bool:
    """Verifica crypto (T16): intenta descifrar una cookie almacenada.

    Distingue 'tabla ausente' (esquema sin migrar → informativo, no fallo) de
    error real de permisos/corrupción/bloqueo (→ FAIL). Devuelve True si ok o
    tabla ausente; SystemExit(1) si la clave no descifra una cookie real.
    """
    from cryptography.fernet import Fernet

    try:
        f = Fernet(fernet_key)
    except Exception as exc:  # clave inválida
        LOG.critical("selfcheck.crypto_bad_key: %r", exc)
        raise SystemExit(1) from exc

    if db_path is None:
        return True  # sin DB aún, nada que verificar

    # Leer una cookie de la DB (tabla ausente = informativo, T16)
    import sqlite3

    try:
        conn = sqlite3.connect(db_path)
    except Exception as exc:  # pragma: no cover
        LOG.critical("selfcheck.crypto_db_error: %r", exc)
        raise SystemExit(1) from exc

    try:
        row = conn.execute(
            "SELECT encrypted_blob FROM cookies LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            LOG.info("selfcheck.crypto_no_cookies_table (informativo, T16)")
            conn.close()
            return True  # tabla ausente → informativo, no fallo
        LOG.critical("selfcheck.crypto_db_error: %r", exc)
        conn.close()
        raise SystemExit(1) from exc
    finally:
        conn.close()

    if row is None:
        return True  # sin cookies almacenadas, nada que descifrar

    try:
        f.decrypt(bytes(row[0]))
    except Exception as exc:  # clave no descifra la cookie → rotación/corrupción
        LOG.critical("selfcheck.crypto_cookie_undecryptable: %r (T16)", exc)
        raise SystemExit(1) from exc

    LOG.info("selfcheck.crypto_ok")
    return True

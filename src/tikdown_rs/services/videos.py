"""Servicio de vídeos — services/videos.py (§4.4, §4.6).

handle_download_result centraliza el resultado de una descarga. Techos de
reintentos (T58) y presupuesto de tiempo (T63); fallos de red no penalizan
(T64).

story: e04s03
"""

from __future__ import annotations

import logging

from tikdown_rs.models.models import Video

LOG = logging.getLogger("tikdown_rs.videos")


def retry_exhausted(video: Video, max_retry: int) -> bool:
    """¿El vídeo agotó el techo de reintentos transitorios? (T58)"""
    return video.retry_count >= max_retry


def should_retry(error_category: str, retry_count: int, max_retry: int) -> bool:
    """¿Se reintenta un fallo? (T58/T64)

    - Fallo de red ('network') → SIEMPRE se reintenta sin consumir techo (T64).
    - Fallo transitorio → se reintenta mientras no supere el techo (T58).
    - Fallo definitivo → no se reintenta (el error es permanente).
    """
    if error_category == "definitive":
        return False
    if error_category == "network":
        return True  # T64: la red no consume reintentos
    # transient (o integrity con margen): reintentar hasta el techo
    return retry_count < max_retry


def apply_retry_penalty(video: Video, error_category: str) -> None:
    """Aplica la penalización de reintentos (T64: red no penaliza)."""
    if error_category == "network":
        return  # T64: no incrementa retry_count ni consume presupuesto
    video.retry_count += 1

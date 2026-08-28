"""Motor de descarga — core/download_engine.py (§4).

Envuelve yt-dlp (curl-cffi/impersonación TLS) para descargar vídeos de TikTok
de forma resistente a bloqueos. Clasifica fallos (§4.3), rota cookies/targets.

story: e04s01
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

LOG = logging.getLogger("tikdown_rs.download_engine")

# Marcadores de autenticación/bloqueo verificados (T52) → definitivo
_AUTH_MARKERS = (
    "requiring login",
    "login required",
    "log into an account",
    "log in for access",
    "permission to view",
    "account is private",
    "captcha",
    "banned",
    "suspended",
    "session expired",
)
# Marcadores de contenido inexistente → definitivo
_GONE_MARKERS = ("video is unavailable", "video unavailable", "404", "removed")
# Marcadores transitorios (T5/T53/T54)
_TRANSIENT_MARKERS = ("status code 0", "keeps sending the same page", "ip address is blocked")


def classify_failure(message: str) -> str:
    """Clasifica un fallo de yt-dlp: 'definitive' | 'transient' | 'integrity'.

    Case-insensitive sobre la cadena completa (incl. causa encadenada), nunca
    subcadenas cortas aisladas (T52). 403 sin hints de auth → transitorio (T5);
    'status code 0' → transitorio (T53); 'same page' → transitorio (T54).
    """
    msg = (message or "").lower()
    for marker in _TRANSIENT_MARKERS:
        if marker in msg:
            return "transient"
    for marker in _AUTH_MARKERS:
        if marker in msg:
            return "definitive"
    for marker in _GONE_MARKERS:
        if marker in msg:
            return "definitive"
    # 403 sin marcadores de auth → transitorio (T5: nunca definitivo)
    if "403" in msg or "forbidden" in msg:
        return "transient"
    return "transient"  # fallback conservador: no asumir definitivo


class DownloadEngine(Protocol):
    """Contrato del motor (inyectado en CLI/bot/daemon, T26)."""

    async def download(self, url: str, archive_path: str | None = None, **kwargs) -> dict:
        """Descarga un vídeo; devuelve resumen con expected_has_video."""
        ...

    async def extract_profile(self, username: str) -> dict:
        """Extrae el perfil de una cuenta (sec_uid, stats)."""
        ...

    async def list_videos(self, username: str) -> list[dict]:
        """Lista vídeos de una cuenta (para backfill/monitor)."""
        ...

    async def validate_cookie(self, cookie_blob: bytes) -> str:
        """Valida una cookie: 'valid' | 'invalid' | 'inconclusive'."""
        ...


# Formato de descarga por defecto (§4.2)
# Formato de descarga por defecto (§4.2). Single-format: TikTok bloquea la
# resolución completa que exige separar video+audio (bug #9 — rehydration).
DEFAULT_FORMAT = "best[height<=1080]/best"
# Formato mejorado para el reintento ante solo-audio (§4.2)
RETRY_FORMAT = "best[ext=mp4]/best"


class YtDlpEngine:
    """Implementación real del motor con yt-dlp (inyectable)."""

    @staticmethod
    def available_impersonate_targets() -> list:
        """Targets ImpersonateTarget de curl-cffi (L-D1).

        Normaliza la forma de retorno de yt-dlp (targets o tuplas
        (target, engine)); vacío si no hay soporte. Exponer aquí mantiene
        yt_dlp fuera de cli/ (regla de oro: cli solo orquesta).
        """
        import yt_dlp

        ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
        raw = getattr(ydl, "_get_available_impersonate_targets", lambda: [])()
        targets: list = []
        for item in raw:
            if isinstance(item, tuple) and item:
                targets.append(item[0])
            else:
                targets.append(item)
        return targets

    def __init__(
        self,
        impersonate_targets: list | None = None,
        download_format: str = "",
        timeout_seconds: int = 600,
        sleep_interval_requests: float = 2.0,
        extractor_retries: int = 8,
        cookies_blob: bytes | None = None,
    ) -> None:
        self._targets = impersonate_targets or []
        self._format = download_format or DEFAULT_FORMAT
        self._timeout = timeout_seconds
        self._sleep_interval_requests = sleep_interval_requests  # T56
        self._extractor_retries = extractor_retries  # T56
        self._zombie_threads: set[int] = set()  # T23/T66: diagnóstico
        self._cookies_blob = cookies_blob  # blob cifrado de cookies (F-01/F-15)

    def _next_target(self):
        """Rotación round-robin de targets (L-D1: objetos ImpersonateTarget)."""
        if not self._targets:
            return None
        target = self._targets.pop(0)
        self._targets.append(target)
        return target

    def _ydl_params(self, target, format_string: str, outtmpl: str) -> dict:
        """Parámetros de yt-dlp para una descarga (L-D1: impersonate objeto)."""
        params = {
            "format": format_string,
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "sleep_interval_requests": self._sleep_interval_requests,  # T56
            "extractor_retries": self._extractor_retries,  # T56
            "retries": 3,
        }
        if target is not None:
            params["impersonate"] = target  # objeto ImpersonateTarget (L-D1)
        if self._cookies_blob:
            # Cookies del blob cifrado (F-01/F-15) → archivo temporal para yt-dlp
            params["cookiefile"] = self._write_cookies_temp()
        return params

    def _write_cookies_temp(self) -> str:
        """Escribe el blob de cookies a un archivo temporal (yt-dlp cookiefile).

        El blob ya viene descifrado (services/backfill lo desencripta con Fernet
        antes de pasarlo al engine). Archivo con permisos 0600, se borra al cierre.
        """
        import tempfile

        if self._cookies_blob is None:
            return ""
        fd, path = tempfile.mkstemp(prefix="tikdown-cookies-", suffix=".txt")
        with os.fdopen(fd, "wb") as f:
            f.write(self._cookies_blob)
        self._zombie_threads.add(1)  # reutilizar el set para trackear tempfiles (T23)
        self._cookie_temp_path = path
        return path

    async def download(self, url: str, archive_path: str | None = None, **kwargs) -> dict:
        """Descarga un vídeo vía to_thread (T12/T23)."""
        import asyncio

        import yt_dlp

        format_string = kwargs.get("format_string", self._format)
        outtmpl = kwargs.get("outtmpl", "%(id)s.%(ext)s")
        # Impersonate opt-in (bug #14): los targets de curl-cffi rompen la
        # descarga en este entorno (rehydration) — solo si se solicitan
        # explícitamente vía kwargs.
        target = self._next_target() if kwargs.get("use_impersonate") else None

        params = self._ydl_params(target, format_string, outtmpl)
        if archive_path:
            params["download_archive"] = archive_path

        def _run() -> dict:
            with yt_dlp.YoutubeDL(params) as ydl:
                info = ydl.extract_info(url, download=True)
            return {"info": info, "target": target}

        def _run() -> dict:
            with yt_dlp.YoutubeDL(params) as ydl:
                info = ydl.extract_info(url, download=True)
            return {"info": info, "target": target}

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run),
                timeout=self._timeout,  # T23
            )
        except TimeoutError:
            LOG.warning("engine.timeout (zombie thread, T23/T66)", extra={"url": url})
            raise

    async def extract_profile(self, username: str) -> dict:
        """Extrae el feed de una cuenta (lista de entradas) sin descargar (T20).

        yt-dlp con download=False lista las entradas del perfil; usado por
        backfill (F-10) y monitor. Devuelve el dict completo de extract_info
        (con 'entries') para que el caller decida.
        """
        import asyncio

        import yt_dlp

        # Sin impersonate en el listado (bug #12): los targets de curl-cffi
        # reducen la extracción del feed (algunos fallan con TikTok y
        # ignoreerrors los descarta). Impersonate solo en download().
        params = self._ydl_params(None, "best", "%(id)s.%(ext)s")
        params["download"] = False
        params["flat_playlist"] = True  # listar URLs sin resolver (T20: el backfill
        # descarga cada vídeo después con download())
        params["ignoreerrors"] = True  # T5: entradas rotas/bloqueadas no abortan el listado
        params["playlistend"] = 50  # límite de listado (ponytail: suficiente para backfill)
        url = f"https://www.tiktok.com/@{username}"

        def _run() -> dict:
            with yt_dlp.YoutubeDL(params) as ydl:
                return ydl.extract_info(url, download=False) or {}

        try:
            info = await asyncio.wait_for(asyncio.to_thread(_run), timeout=self._timeout)
        except TimeoutError:
            LOG.warning("engine.extract_profile.timeout", extra={"username": username})
            raise
        # yt-dlp con ignoreerrors deja entradas None (bloqueadas) — filtrar (T5).
        # bug #15: el feed con cookies puede devolver URLs de CDN con query
        # strings gigantes (id monstruoso → 'File name too long' al descargar).
        # Normalizar cada entrada a URL de PÁGINA (https://www.tiktok.com/@u/video/<id>)
        # para que download() genere IDs cortos y TikTok la resuelva bien.
        entries: list[dict] = []
        for e in (info or {}).get("entries") or []:
            if e is None:
                continue
            vid_id = e.get("id")
            if vid_id:
                e["url"] = f"https://www.tiktok.com/@{username}/video/{vid_id}"
            entries.append(e)
        return {"entries": entries}

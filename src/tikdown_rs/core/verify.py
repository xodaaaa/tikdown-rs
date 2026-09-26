"""Validation probe primitive over yt-dlp profile listing (M1).

Trampas neutralizadas: T-ENGINE-11, T-ENGINE-13, T-COOKIES-1. Regla: 4.1, 4.6, 7.

This module is the M1 PROBE PROVIDER: a primitive to be COMPOSED by the M2
engine (candidate rotation across COOKIE_VALIDATION_URL, per-cycle primary
rotation, 6h revalidation job), never duplicated. Nothing here classifies
errors or persists state: exceptions propagate to the caller, which applies
core.errors.classify_error (T-DATA-3: one project-wide classifier).
"""

import logging
from typing import Any

import yt_dlp

from tikdown_rs.core.cookie_parser import write_canonical_netscape_tempfile

logger = logging.getLogger("tikdown_rs.core.verify")

_NO_ACCESSOR = object()  # sentinel: the private accessor is absent/unusable


def probe_profile(cookies_blob: bytes, profile_url: str, max_entries: int) -> list[dict]:
    """List the first ``max_entries`` entries of a profile feed using ``cookies_blob``.

    Option surface (4.6):
    - ``flat_playlist=True`` ALWAYS: list URLs, never resolve each video
      (T-ENGINE-12: without it TikTok blocks intermittently).
    - ``ignoreerrors=True`` and None entries FILTERED (T-ENGINE-13: iterating
      a None entry raises AttributeError).
    - NO ``impersonate`` at parameter level, ever (T-ENGINE-11/T-ENGINE-15).

    T-COOKIES-1/6/7: the blob is materialized through the canonical Netscape
    tempfile writer (magic header, immediate fd close, best-effort cleanup in
    ``finally``). Exceptions propagate: the caller classifies them (7).
    """
    tmp_path = write_canonical_netscape_tempfile(cookies_blob.decode("utf-8"))
    try:
        options = {
            "flat_playlist": True,  # T-ENGINE-12
            "ignoreerrors": True,
            "playlistend": max_entries,
            "cookiefile": str(tmp_path),
            # T-ENGINE-11: no impersonate parameter, ever.
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(profile_url, download=False)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)  # T-COOKIES-6, best-effort
        except OSError as exc:
            logger.warning("could not delete cookies tempfile %s (%s)", tmp_path, exc)
    entries: list[Any] = (info or {}).get("entries") or []
    return [entry for entry in entries if entry is not None]  # T-ENGINE-13


def entries_have_video(entries: list[dict]) -> bool:
    """True if ANY entry looks like a video (T-COOKIES-2).

    The FIRST entry may be an audio-only slideshow even on good profiles, so
    all ``max_entries`` are scanned: a slideshow-only feed returns False.
    """
    for entry in entries:
        if entry.get("duration"):
            return True
        if "/video/" in (entry.get("url") or ""):
            return True
    return False


def probe_impersonation() -> tuple[bool, str, int]:
    """Three-layer impersonation capacity probe; returns (available, reason, count).

    NEVER raises (Regla 4.1, T-ENGINE-22). Layers, in order:
    1. The PRIVATE accessor ``YoutubeDL._get_available_impersonate_targets``
       when present, with defensive normalization of BOTH return shapes
       (objects vs strings, ``(target, handler)`` tuples vs plain targets,
       T-ENGINE-6).
    2. ``import curl_cffi``: importable-but-count-0 -- importing is NOT having
       targets (Regla 4.1).
    3. Unavailable with cause ``curl_cffi-missing``.

    Distinguishing "not available" from "could not inspect" is the T-ENGINE-22
    rule: an API shape change degrades the DIAGNOSIS, never the operation. Any
    inspection failure is logged with exc_info as
    ``selfcheck.impersonation_api_changed`` and reported as
    ``(False, "inspection-failed", 0)``.
    """
    try:
        ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
        accessor = getattr(ydl, "_get_available_impersonate_targets", None)
        raw = accessor() if accessor is not None else _NO_ACCESSOR
    except Exception:  # layer 1 unusable: fall through to layer 2 (T-ENGINE-22)
        logger.warning("selfcheck.impersonation_api_changed", exc_info=True)
        raw = _NO_ACCESSOR

    if raw is not _NO_ACCESSOR:
        try:
            targets = [item[0] if isinstance(item, tuple) else item for item in raw]
        except Exception:  # garbage return shape: could not inspect (T-ENGINE-22)
            logger.warning("selfcheck.impersonation_api_changed", exc_info=True)
            return False, "inspection-failed", 0
        if targets:
            return True, "private-api", len(targets)
    # No accessor, unusable accessor, or an EMPTY target list -> layer 2.

    try:
        import curl_cffi  # noqa: F401

        return True, "curl_cffi-importable", 0  # importing != having targets: 4.1
    except Exception:  # noqa: BLE001 - any import failure is the third classic cause
        return False, "curl_cffi-missing", 0

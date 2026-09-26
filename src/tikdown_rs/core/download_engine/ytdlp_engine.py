"""yt-dlp concrete engine, listing and validation surface (M2/T2).

Trampas neutralizadas: T-BACKFILL-12, T-ENGINE-11, T-ENGINE-12, T-ENGINE-13,
T-ENGINE-14, T-ENGINE-23, T-ENGINE-25, T-COOKIES-6, T-COOKIES-7. Reglas:
4.2, 4.6, 4.8.

Listing rules (4.6): ``flat_playlist=True`` ALWAYS (T-ENGINE-12);
``ignoreerrors=True`` with None entries filtered (T-ENGINE-13); NO
``impersonate`` at parameter level, ever (T-ENGINE-11/T-ENGINE-15); every
entry normalized to the canonical page URL (T-ENGINE-14); ``upload_date``
normalized to YYYYMMDD (T-ENGINE-25).

Cookies (4.3, T-BACKFILL-12): the engine REQUIRES a cookies blob; loading
cookies is the entry point's responsibility and there is no silent default.
The blob is materialized through the canonical Netscape tempfile writer
(T-COOKIES-1/6/7) around each extraction and deleted in ``finally``.

Layering (4.8): ``validate_cookie`` composes the M1 primitives
``core.verify.probe_profile`` + ``core.verify.entries_have_video`` directly;
core must not import services, so services/cookies.validate_cookie (which
persists state) stays the caller-facing path and the engine only exposes the
probe composition with the same three-state semantics (T-COOKIES-2/3).
"""

import asyncio
import logging
import re
from pathlib import Path

import yt_dlp

from tikdown_rs.core.archive import DownloadArchive
from tikdown_rs.core.config import Settings
from tikdown_rs.core.cookie_parser import write_canonical_netscape_tempfile
from tikdown_rs.core.download_engine.protocol import ProbeFn, ProfileData, VideoData
from tikdown_rs.core.errors import ConfigurationError, DownloadTimeoutError, classify_error
from tikdown_rs.core.paths import outtmpl_for
from tikdown_rs.core.verify import entries_have_video, probe_profile

logger = logging.getLogger("tikdown_rs.core.download_engine")

# 4.2 format chain.
DEFAULT_FORMAT = "best[height<=1080]/best"  # progressive first (T-ENGINE-10)
FALLBACK_FORMAT = "bestvideo[height<=1080]+bestaudio/best"  # DASH as last resort
MERGE_OUTPUT_FORMAT = "mp4"

# 4.2 hardening internals: sleep_interval_requests 1-3 s (T-ENGINE-23),
# socket_timeout 20-30 s. Constants here because Settings carries no fields
# for them yet; they move to Settings in the unit that wires them.
SLEEP_INTERVAL_REQUESTS = 1
SOCKET_TIMEOUT = 20

_UPLOAD_DATE_PARTS_RE = re.compile(r"[-/.]")


def normalize_upload_date(value: object) -> str:
    """Canonical YYYYMMDD (T-ENGINE-25); ``""`` when absent or unparseable.

    The empty string is the defined fallback: the caller assigns the previous
    updated cursor to a video without upload_date (4.6, T-BACKFILL-3), never
    NULL and never a stale value.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if text.isdigit():
        return text if len(text) == 8 else ""
    parts = _UPLOAD_DATE_PARTS_RE.split(text)
    if len(parts) == 3:
        return "".join(part.zfill(2) for part in parts)
    return ""


def canonical_video_url(username: str, video_id: str) -> str:
    """Canonical TikTok page URL (4.6, T-ENGINE-14)."""
    return f"https://www.tiktok.com/@{username}/video/{video_id}"


class YtDlpEngine:
    """Concrete DownloadEngine over yt-dlp (4.8)."""

    def __init__(self, cookies_blob: bytes, settings: Settings) -> None:
        if not cookies_blob:
            raise ConfigurationError(
                "YtDlpEngine requires a cookies blob: loading cookies is the "
                "entry point's responsibility, there is no silent default "
                "(T-BACKFILL-12)"
            )
        self._cookies_blob = cookies_blob
        self._settings = settings
        # T-ASYNC-14/15: timed-out yt-dlp native threads that wait_for cannot
        # kill; the pacer/daemon expose this counter in daemon status later.
        self.zombie_threads = 0

    async def download(
        self,
        page_url: str,
        video_id: str,
        uploader: str | None,
        retry_index: int = 0,
        archive: DownloadArchive | None = None,
    ) -> Path:
        """Download one video; returns the final downloaded file Path (4.5).

        ``uploader`` is accepted for the caller's contract: yt-dlp fills
        ``%(uploader)s`` in the outtmpl from its own extraction metadata, and
        the feed layer already normalizes entries to the canonical page URL
        (T-ENGINE-14).

        Format chain (4.2, T-ENGINE-10): ``settings.download_format`` override
        runs ALONE (operator decision, no fallback); otherwise DEFAULT_FORMAT
        first and FALLBACK_FORMAT as last resort. The SAME ``--download-archive``
        file path is passed on BOTH funnel calls, and the archive entry for
        ``video_id`` is DISCARDED before the fallback attempt, or yt-dlp would
        answer 'already downloaded' (T-ENGINE-18). ``merge_output_format`` is
        always mp4 (4.2).

        Timeouts (T-ASYNC-8/14/15): the blocking yt-dlp call runs inside
        ``asyncio.to_thread``; ``asyncio.wait_for`` does NOT kill the native
        thread, so on timeout the zombie is counted in ``zombie_threads`` and
        DownloadTimeoutError is raised -- classifying it is the caller's job.
        The retry outtmpl writes ``%(id)s.retry-N`` so a still-writing zombie
        can never collide with the retry's file; renaming after integrity is
        the CALLER's job in the next unit, never here.
        """
        outtmpl = self._download_outtmpl(retry_index)
        override = self._settings.download_format
        formats = [override] if override else [DEFAULT_FORMAT, FALLBACK_FORMAT]
        for attempt, fmt in enumerate(formats):
            options: dict = {
                "format": fmt,
                "outtmpl": outtmpl,
                "merge_output_format": MERGE_OUTPUT_FORMAT,
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": SOCKET_TIMEOUT,
            }
            if archive is not None:
                options["download_archive"] = str(archive.archive_path)
            try:
                info = await asyncio.wait_for(
                    asyncio.to_thread(self._download_sync, page_url, options),
                    timeout=self._settings.download_timeout_seconds,
                )
            except TimeoutError:
                # T-ASYNC-14/15: wait_for cancels the awaitable but the native
                # yt-dlp thread keeps running as a zombie; count it. No fallback:
                # a timeout is not a format problem.
                self.zombie_threads += 1
                raise DownloadTimeoutError(
                    f"download of {video_id} exceeded download_timeout_seconds="
                    f"{self._settings.download_timeout_seconds}"
                ) from None
            except Exception:
                if attempt == len(formats) - 1:
                    raise
                # T-ENGINE-18: discard the archive entry BEFORE the fallback
                # attempt, or yt-dlp answers 'already downloaded'.
                if archive is not None:
                    await archive.remove(video_id)
                continue
            return self._resolve_downloaded_path(info)
        raise AssertionError("unreachable: the format chain is never empty")

    @staticmethod
    def _resolve_downloaded_path(info: dict | None) -> Path:
        """Final file Path from yt-dlp's documented return value.

        ``extract_info(url, download=True)`` returns the info dict; for a real
        download it carries ``requested_downloads`` whose entries hold
        ``filepath`` updated to the FINAL path by MoveFilesAfterDownloadPP
        (locked source: yt_dlp/YoutubeDL.py process_video_result). An empty
        ``requested_downloads`` means nothing was downloaded (e.g. the video is
        already in the archive, T-ENGINE-18 context): that outcome is surfaced
        to the caller, never silently swallowed and never a format fallback.
        """
        for entry in (info or {}).get("requested_downloads") or []:
            filepath = entry.get("filepath")
            if filepath:
                return Path(filepath)
        raise RuntimeError(
            "yt-dlp returned no downloaded file (requested_downloads empty): "
            "the video may already be in the download archive (T-ENGINE-18)"
        )

    def _download_outtmpl(self, retry_index: int) -> str:
        """outtmpl from core/paths (4.5); retries append ``.retry-N`` (T-ASYNC-14/15)."""
        outtmpl = outtmpl_for(self._settings.data_dir)
        if retry_index > 0:
            outtmpl = outtmpl.replace("%(id)s.%(ext)s", f"%(id)s.retry-{retry_index}.%(ext)s")
        return outtmpl

    def _download_sync(self, page_url: str, options: dict) -> dict | None:
        """Blocking yt-dlp download; ALWAYS run inside ``asyncio.to_thread`` (T-ASYNC-8)."""
        tmp_path = write_canonical_netscape_tempfile(self._cookies_blob.decode("utf-8"))
        try:
            options = {**options, "cookiefile": str(tmp_path)}
            with yt_dlp.YoutubeDL(options) as ydl:
                return ydl.extract_info(page_url, download=True)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("could not delete cookies tempfile %s (%s)", tmp_path, exc)

    def extract_profile(self, username: str) -> ProfileData:
        """Profile refresh info from the first flat page (4.6)."""
        username = username.lstrip("@")
        info = self._extract(
            f"https://www.tiktok.com/@{username}", self._flat_options(max_entries=1)
        )
        info = info or {}
        return ProfileData(
            username=username,
            followers=info.get("channel_follower_count"),
            video_count=info.get("playlist_count"),
        )

    def list_videos(self, username: str, max_entries: int | None = None) -> list[VideoData]:
        """Normalized feed listing (4.6 rules; see module docstring)."""
        username = username.lstrip("@")
        info = self._extract(f"https://www.tiktok.com/@{username}", self._flat_options(max_entries))
        videos: list[VideoData] = []
        for entry in (info or {}).get("entries") or []:
            if entry is None:  # T-ENGINE-13: ignoreerrors leaves None entries
                continue
            video_id = entry.get("id")
            if not video_id:  # no id: no canonical page URL can be built
                continue
            videos.append(
                VideoData(
                    id=str(video_id),
                    url=canonical_video_url(username, str(video_id)),  # T-ENGINE-14
                    title=entry.get("title"),
                    description=entry.get("description"),
                    duration=entry.get("duration"),
                    upload_date=normalize_upload_date(entry.get("upload_date")),  # T-ENGINE-25
                    uploader=entry.get("uploader") or username,
                )
            )
        return videos

    def validate_cookie(self, probe_fn: ProbeFn | None = None) -> str:
        """Three-state verdict composing the M1 probe primitives (4.8).

        Candidates come from ``settings.cookie_validation_url`` in order. Only
        an auth-CONFIRMED failure (classify_error -> 'definitive', 4.4 rule 2)
        returns 'invalid'; any other failure or a feed without video entries
        (T-COOKIES-2: the first entry may be a slideshow) tries the next
        candidate; exhausting all candidates returns 'inconclusive' without
        any state change (T-COOKIES-3: a broken probe never invalidates).

        ``probe_fn(blob, url, max_entries) -> list[dict]`` is injectable for
        tests; the default is ``core.verify.probe_profile`` and the result is
        evaluated with ``core.verify.entries_have_video``. services/cookies
        .validate_cookie stays the persistence path on top of this composition.
        """
        probe = probe_fn or probe_profile
        max_entries = self._settings.cookie_probe_max_entries
        for url in self._settings.cookie_validation_url:
            try:
                entries = probe(self._cookies_blob, url, max_entries)
            except Exception as exc:  # noqa: BLE001 - any failure is classified
                if classify_error(exc) == "definitive":
                    return "invalid"  # auth confirmed against a probe candidate
                continue  # transient: try the next candidate URL
            if entries_have_video(entries):
                return "valid"
        return "inconclusive"

    def _flat_options(self, max_entries: int | None) -> dict:
        """Mandatory 4.6 listing options; shared by list_videos/extract_profile."""
        options = {
            "flat_playlist": True,  # T-ENGINE-12: ALWAYS
            "ignoreerrors": True,  # T-ENGINE-13 companion
            "quiet": True,
            "no_warnings": True,
            "sleep_interval_requests": SLEEP_INTERVAL_REQUESTS,  # T-ENGINE-23
            "socket_timeout": SOCKET_TIMEOUT,
            # T-ENGINE-11/T-ENGINE-15: NO impersonate at parameter level, ever.
        }
        if max_entries is not None:
            options["playlistend"] = max_entries
        return options

    def _extract(self, url: str, options: dict) -> dict | None:
        """Run one flat extraction with the blob materialized as a tempfile.

        T-COOKIES-1/6/7: canonical Netscape tempfile (magic header, immediate
        fd close), deleted in ``finally`` even on success or failure.
        """
        tmp_path = write_canonical_netscape_tempfile(self._cookies_blob.decode("utf-8"))
        try:
            options = {**options, "cookiefile": str(tmp_path)}
            with yt_dlp.YoutubeDL(options) as ydl:
                return ydl.extract_info(url, download=False)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("could not delete cookies tempfile %s (%s)", tmp_path, exc)

"""DownloadEngine Protocol (4.8).

Trampas neutralizadas: T-ENGINE-16, T-ENGINE-17, T-DEPLOY-20. Regla: 4.8.

Every method declared here MUST have a real implementation on the concrete
class with a contract test that instantiates it and calls each method
(T-ENGINE-16): a Protocol method without a real implementation breaks the
whole flow with AttributeError, and a double implementing only what a test
needs is exactly what hid that bug. Naming is unambiguous: ``DownloadEngine``
(yt-dlp) vs SQLAlchemy's ``db_engine`` (T-ENGINE-17), never a bare ``engine``.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypedDict, runtime_checkable

from tikdown_rs.core.archive import DownloadArchive

ProbeFn = Callable[[bytes, str, int], list[dict]]


class VideoData(TypedDict):
    """Minimal normalized video info, modeled on what yt-dlp entries provide.

    ``url`` is ALWAYS the canonical page URL
    ``https://www.tiktok.com/@{username}/video/{id}`` (T-ENGINE-14) and
    ``upload_date`` is ALWAYS ``YYYYMMDD`` or ``""`` (T-ENGINE-25; the caller
    assigns the previous updated cursor, T-BACKFILL-3).
    """

    id: str
    url: str
    title: str | None
    description: str | None
    duration: float | None
    upload_date: str
    uploader: str


class ProfileData(TypedDict):
    """Minimal profile refresh info per 3.1 (avatar-free)."""

    username: str
    followers: int | None
    video_count: int | None


@runtime_checkable
class DownloadEngine(Protocol):
    """The engine surface the rest of the system programs against (4.8)."""

    async def download(
        self,
        page_url: str,
        video_id: str,
        uploader: str | None,
        retry_index: int = 0,
        archive: DownloadArchive | None = None,
    ) -> Path:
        """Download one video; returns the final file Path (4.5).

        Trampas neutralizadas: T-ENGINE-18 (archive on both funnel calls,
        discard before fallback), T-ASYNC-8/14/15 (to_thread + zombie
        accounting, .retry-N outtmpl). Implemented in M2/T4.
        """
        ...

    def extract_profile(self, username: str) -> ProfileData:
        """Profile refresh info (followers, video_count) for 3.1."""
        ...

    def list_videos(self, username: str, max_entries: int | None = None) -> list[VideoData]:
        """Normalized feed listing (4.6 rules)."""
        ...

    def validate_cookie(self, probe_fn: ProbeFn | None = None) -> str:
        """Three-state cookie verdict: 'valid' | 'invalid' | 'inconclusive'."""
        ...

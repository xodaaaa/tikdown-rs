"""Data-dir derived paths (4.5).

Trampas neutralizadas: T-BACKFILL-21 (relative video paths escape the Docker
volume: every path derives from DATA_DIR via this module, never from cwd).
Regla: 4.5.
"""

from pathlib import Path


def videos_root(data_dir: Path | str) -> Path:
    """``<data_dir>/videos`` (4.5, T-BACKFILL-21)."""
    return Path(data_dir) / "videos"


def outtmpl_for(data_dir: Path | str) -> str:
    """yt-dlp outtmpl under ``videos_root`` (4.5, T-BACKFILL-21, T-ENGINE-14).

    ``%(id)s`` is the video id extracted from the CANONICAL page URL: the
    engine normalizes every feed entry to
    ``https://www.tiktok.com/@{username}/video/{id}`` before anything reaches
    yt-dlp, so with-cookie CDN URLs with giant query strings can never leak
    into file names (T-ENGINE-14: OSError: File name too long).
    """
    return str(videos_root(data_dir) / "%(uploader)s" / "%(id)s.%(ext)s")

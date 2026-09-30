"""download_archive: text file source of truth + queryable mirror (§3.6).

Trampas neutralizadas: T-DB-8, T-DB-16, T-DEPLOY-15. Regla: §3.6.

The text file ``<DATA_DIR>/download_archive.txt`` is the SOURCE OF TRUTH for
yt-dlp ``--download-archive`` (append-only); the ``download_archive`` table is
the queryable mirror. The parser recognizes BOTH line formats (``tiktok <id>``
and bare ``<id>``): the ID is the LAST whitespace-separated token (T-DB-8:
yt-dlp writes ``tiktok <id>`` while other writers may emit a bare id).

Methods are async because the project session factory is an
``async_sessionmaker`` over aiosqlite (§3.7): every method opens a short
session (T-DB-15) and commits internally (T-DB-13).
"""

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.models.download_archive import DownloadArchive as DownloadArchiveRow

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class DownloadArchive:
    """Append-only archive file (source of truth) + queryable mirror (§3.6)."""

    def __init__(
        self, archive_path: Path, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self.archive_path = Path(archive_path)
        self._session_factory = session_factory

    @staticmethod
    def parse_line(line: str) -> str | None:
        """ID = LAST whitespace-separated token (T-DB-8); None for blank/comment lines."""
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return None
        return stripped.split()[-1]

    async def add(self, video_id: str, account_id: int | None = None) -> None:
        """Append ``tiktok <id>`` to the file FIRST, then mirror it (§3.6).

        File first: the file is yt-dlp's source of truth. The mirror insert is
        best-effort: on failure the file entry is kept and only a warning is
        logged (M10: the no-caller rebuild helper was retired, plan §0.3).
        """
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)

        def _append() -> None:
            with self.archive_path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(f"tiktok {video_id}\n")

        # T-ASYNC-8: file I/O never runs on the event loop.
        await asyncio.to_thread(_append)
        try:
            async with self._session_factory() as session:
                session.add(
                    DownloadArchiveRow(
                        tiktok_id=video_id, account_id=account_id, created_at=_utcnow_iso()
                    )
                )
                await session.commit()
        except Exception:
            logger.warning(
                "download_archive mirror insert failed for %s; file entry kept",
                video_id,
                exc_info=True,
            )

    async def contains(self, video_id: str) -> bool:
        """Mirror-table query (§3.6); an empty or missing file means not contained."""
        async with self._session_factory() as session:
            found = await session.scalar(
                select(DownloadArchiveRow.id)
                .where(DownloadArchiveRow.tiktok_id == video_id)
                .limit(1)
            )
        return found is not None

    async def remove(self, video_id: str) -> bool:
        """Discard the entry from file AND mirror (4.5, T-ENGINE-18).

        Called BEFORE a fallback download attempt: without this yt-dlp answers
        'already downloaded'. File first (source of truth), mirror best-effort
        with a logged warning. Returns True when the file contained the entry.

        T-DB-16: the rewrite goes through a sibling temp file + ``os.replace``
        (the same atomic pattern as services/maintenance.py) — yt-dlp appends
        to this file concurrently, and an in-place ``open("w")`` rewrite had a
        truncate window that destroyed the history on a mid-write crash. The
        whole read+write+replace block runs in ``to_thread`` (T-ASYNC-8).
        """

        def _rewrite() -> bool:
            if not self.archive_path.exists():
                return False
            lines = self.archive_path.read_text(encoding="utf-8").splitlines(keepends=True)
            kept: list[str] = []
            found = False
            for index, line in enumerate(lines):
                complete = line.endswith("\n") or index < len(lines) - 1
                if complete and self.parse_line(line) == video_id:
                    found = True
                    continue
                kept.append(line)
            if found:
                tmp = self.archive_path.with_name(
                    f"{self.archive_path.name}.tmp-{os.getpid()}-{uuid4().hex[:8]}"
                )
                try:
                    with tmp.open("w", encoding="utf-8", newline="\n") as file:
                        file.writelines(kept)
                    # Atomic swap: readers/appenders never see a truncate window.
                    os.replace(tmp, self.archive_path)
                except BaseException:
                    tmp.unlink(missing_ok=True)  # best-effort temp cleanup
                    raise
            return found

        removed = await asyncio.to_thread(_rewrite)
        try:
            async with self._session_factory() as session:
                await session.execute(
                    delete(DownloadArchiveRow).where(DownloadArchiveRow.tiktok_id == video_id)
                )
                await session.commit()
        except Exception:
            logger.warning("download_archive mirror delete failed for %s", video_id, exc_info=True)
        return removed

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
        self._recover_staged_rewrite()

    def _staged_tmp_path(self) -> Path:
        """Deterministic staging name: the crash marker for T-DB-16 recovery."""
        return self.archive_path.with_name(self.archive_path.name + ".tmp")

    def _recover_staged_rewrite(self) -> None:
        """T-DB-16 crash recovery: a leftover staged temp means the previous
        remove() crashed between staging and the in-place write. The temp holds
        the kept content, so publish it (no yt-dlp writer can hold the archive
        open at construction time — the engine is built after this)."""
        staged = self._staged_tmp_path()
        if staged.exists():
            try:
                os.replace(staged, self.archive_path)
            except OSError:
                logger.warning("download_archive staged rewrite recovery failed", exc_info=True)

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

        T-DB-16 + R3-001: NO ``os.replace`` of the archive itself — it strands
        yt-dlp's open ``O_APPEND`` descriptors on the old inode and discards
        appends that land between the read and the swap. Instead: stage the
        kept content in an fsynced deterministic temp, then rewrite IN PLACE
        (open descriptors keep working, same as the pre-audit code). A crash
        between staging and the in-place write leaves the temp as the recovery
        marker; the next ``__init__`` publishes it. The whole block runs in
        ``to_thread`` (T-ASYNC-8).
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
            if not found:
                return False
            staged = self._staged_tmp_path()
            # No try/except around the staging: a crash KEEPS the staged temp
            # on purpose — it is the crash-recovery marker (__init__ restores
            # it). Success deletes it below.
            with staged.open("w", encoding="utf-8", newline="\n") as file:
                file.writelines(kept)
                file.flush()
                os.fsync(file.fileno())
            with self.archive_path.open("w", encoding="utf-8", newline="\n") as file:
                file.writelines(kept)
                file.flush()
                os.fsync(file.fileno())
            staged.unlink(missing_ok=True)
            return True

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

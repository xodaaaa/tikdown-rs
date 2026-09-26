"""download_archive: text file source of truth + queryable mirror (§3.6).

Trampas neutralizadas: T-DB-8, T-DEPLOY-15. Regla: §3.6.

The text file ``<DATA_DIR>/download_archive.txt`` is the SOURCE OF TRUTH for
yt-dlp ``--download-archive`` (append-only); the ``download_archive`` table is
the queryable mirror. The parser recognizes BOTH line formats (``tiktok <id>``
and bare ``<id>``): the ID is the LAST whitespace-separated token (T-DB-8:
yt-dlp writes ``tiktok <id>`` while other writers may emit a bare id). The
file reader skips a malformed final line without a trailing newline
(T-DEPLOY-15: a crash mid-append must not poison parsing).

Methods are async because the project session factory is an
``async_sessionmaker`` over aiosqlite (§3.7): every method opens a short
session (T-DB-15) and commits internally (T-DB-13).
"""

import logging
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
        logged (``sync_from_file`` can rebuild the row later).
        """
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)
        with self.archive_path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(f"tiktok {video_id}\n")
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

    async def sync_from_file(self) -> int:
        """Rebuild missing mirror rows from the file; returns the count added.

        The mirror stays authoritative for runtime queries; this rebuild covers
        mirror insert failures (add is best-effort) and is the parser's test
        surface. Idempotent: already-mirrored ids are skipped.
        """
        added = 0
        async with self._session_factory() as session:
            existing = set(await session.scalars(select(DownloadArchiveRow.tiktok_id)))
            for video_id in self._read_ids():
                if video_id in existing:
                    continue
                session.add(DownloadArchiveRow(tiktok_id=video_id, created_at=_utcnow_iso()))
                added += 1
            await session.commit()
        return added

    async def remove(self, video_id: str) -> bool:
        """Discard the entry from file AND mirror (4.5, T-ENGINE-18).

        Called BEFORE a fallback download attempt: without this yt-dlp answers
        'already downloaded'. File first (source of truth), mirror best-effort
        with a logged warning. Returns True when the file contained the entry.
        """
        removed = False
        if self.archive_path.exists():
            lines = self.archive_path.read_text(encoding="utf-8").splitlines(keepends=True)
            kept: list[str] = []
            for index, line in enumerate(lines):
                complete = line.endswith("\n") or index < len(lines) - 1
                if complete and self.parse_line(line) == video_id:
                    removed = True
                    continue
                kept.append(line)
            if removed:
                with self.archive_path.open("w", encoding="utf-8", newline="\n") as file:
                    file.writelines(kept)
        try:
            async with self._session_factory() as session:
                await session.execute(
                    delete(DownloadArchiveRow).where(DownloadArchiveRow.tiktok_id == video_id)
                )
                await session.commit()
        except Exception:
            logger.warning("download_archive mirror delete failed for %s", video_id, exc_info=True)
        return removed

    def _read_ids(self) -> list[str]:
        """Parse the archive file (T-DB-8 last-token rule, T-DEPLOY-15 tail skip)."""
        try:
            text = self.archive_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        ids: list[str] = []
        for line in text.splitlines(keepends=True):
            if not line.endswith("\n"):
                break  # T-DEPLOY-15: partial last line (no trailing newline), skip
            parsed = self.parse_line(line)
            if parsed is not None:
                ids.append(parsed)
        return ids

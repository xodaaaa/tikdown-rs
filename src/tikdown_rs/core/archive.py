"""download_archive: append-only text file, single source of truth (§3.6).

Trampas neutralizadas: T-DB-8, T-DB-16. Regla: §3.6.

The text file ``<DATA_DIR>/download_archive.txt`` is the SINGLE SOURCE OF
TRUTH for yt-dlp ``--download-archive`` (append-only). The former queryable
mirror table was removed (plan §3.6 amendment, migration
``0004_drop_download_archive``): no database replica exists anymore. The
parser recognizes BOTH line formats (``tiktok <id>`` and bare ``<id>``):
the ID is the LAST whitespace-separated token (T-DB-8: yt-dlp writes
``tiktok <id>`` while other writers may emit a bare id).
"""

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class DownloadArchive:
    """Append-only archive file (single source of truth) (§3.6)."""

    def __init__(self, archive_path: Path) -> None:
        self.archive_path = Path(archive_path)
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
        """Append ``tiktok <id>`` to the file (§3.6).

        The file is the single source of truth. ``account_id`` is kept in the
        signature for caller compatibility; with the mirror table removed it is
        no longer persisted anywhere.
        """
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)

        def _append() -> None:
            with self.archive_path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(f"tiktok {video_id}\n")

        # T-ASYNC-8: file I/O never runs on the event loop.
        await asyncio.to_thread(_append)

    async def remove(self, video_id: str) -> bool:
        """Discard the entry from the file (4.5, T-ENGINE-18).

        Called BEFORE a fallback download attempt: without this yt-dlp answers
        'already downloaded'. Returns True when the file contained the entry.

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

        return await asyncio.to_thread(_rewrite)

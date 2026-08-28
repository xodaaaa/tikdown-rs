"""Archivo de deduplicación — core/archive.py (§4.5).

Dedupe append-only (download_archive.txt) + tabla SQLite complementaria.
Parser tolerante: ambos formatos de línea (L-C8), última línea malformada
(T47). discard() con reescritura atómica (T24: antes del reintento).

story: e04s01
"""

from __future__ import annotations

import os
from pathlib import Path


class DownloadArchive:
    """Archivo de deduplicación de vídeos ya descargados."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _ids(self) -> set[str]:
        """Conjunto de IDs, tolerando ambos formatos (L-C8) y línea malformada (T47)."""
        ids: set[str] = set()
        if not self.path.exists():
            return ids
        for line in self.path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            tokens = line.split()
            if not tokens:
                continue
            # L-C8: el ID es el último token ('tiktok <id>' o '<id>' pelado)
            last = tokens[-1]
            if last and last.isdigit() and len(last) >= 5:
                ids.add(last)
        return ids

    def contains(self, video_id: str) -> bool:
        return video_id in self._ids()

    def add(self, video_id: str) -> None:
        """Añade un ID; tolera duplicados físicos (no duplica)."""
        if self.contains(video_id):
            return
        # Append + flush + fsync (escritura atómica por línea corta, §4.5)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"tiktok {video_id}\n")
            f.flush()
            os.fsync(f.fileno())

    def discard(self, video_id: str) -> None:
        """Elimina un ID con reescritura atómica (T24: previo al reintento)."""
        ids = self._ids()
        if video_id not in ids:
            return
        ids.discard(video_id)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for vid in sorted(ids):
                f.write(f"tiktok {vid}\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

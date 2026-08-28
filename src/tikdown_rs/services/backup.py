"""Backup de la base de datos — services/backup.py (§3, F-21b, §23.3.6).

Snapshot consistente en caliente con VACUUM INTO (nunca copiar el .db a pelo
bajo WAL, F-21b). Retención SYSTEM_BACKUP_RETAIN_COUNT (default 7) con purga
best-effort (T14).

story: e09s02
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from tikdown_rs.core.config import Settings

LOG = logging.getLogger("tikdown_rs.backup")


def _backups_dir(settings: Settings) -> Path:
    return settings.data_dir / "backups"


def create_backup(settings: Settings) -> Path:
    """Crea un snapshot VACUUM INTO (F-21b) y purga los antiguos (§23.3.6).

    Returns: la ruta del snapshot creado.
    """
    backups = _backups_dir(settings)
    backups.mkdir(parents=True, exist_ok=True)
    db = settings.data_dir / "tikdown-rs.db"
    if not db.exists():
        raise FileNotFoundError(f"base de datos no encontrada: {db}")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    snapshot = backups / f"tikdown-rs-{stamp}.db"
    # Sufijo de colisión: varios backups en el mismo segundo (tests/uso rápido)
    n = 1
    while snapshot.exists():
        snapshot = backups / f"tikdown-rs-{stamp}-{n}.db"
        n += 1

    conn = sqlite3.connect(db)
    try:
        # VACUUM INTO: snapshot consistente en caliente (seguro bajo WAL, F-21b)
        conn.execute(f"VACUUM INTO '{snapshot}'")
    finally:
        conn.close()

    LOG.info("backup.created", extra={"snapshot": str(snapshot)})
    # Retención (§23.3.6): purga los más antiguos por encima del límite
    purge_old_backups(settings)
    return snapshot


def purge_old_backups(settings: Settings) -> None:
    """Purga snapshots antiguos por encima de SYSTEM_BACKUP_RETAIN_COUNT (T14)."""
    retain = settings.system_backup_retain_count
    backups = sorted(_backups_dir(settings).glob("tikdown-rs-*.db"))
    for old in backups[:-retain] if retain > 0 else backups:
        try:
            old.unlink()
            LOG.info("backup.purged", extra={"snapshot": str(old)})
        except OSError as exc:
            # T14: best-effort — un fallo de borrado no rompe el backup
            LOG.warning("backup.purge_failed", extra={"exc": repr(exc)})

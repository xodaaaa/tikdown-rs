"""e09s02 — backup: VACUUM INTO (F-21b), retención (T14), error limpio."""

# story: e09s02
import sqlite3
from pathlib import Path

import pytest

from tikdown_rs.core.config import Settings
from tikdown_rs.services.backup import create_backup


def _make_db(data_dir: Path, name: str = "tikdown-rs.db") -> Path:
    """Crea una DB de prueba con una tabla y un dato."""
    db = data_dir / name
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    conn.close()
    return db


def test_create_backup_vacuum_into(tmp_path):
    """F-21b: VACUUM INTO crea un snapshot con datos recuperables."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _make_db(data_dir)
    settings = Settings(_env_file=None, data_dir=data_dir)

    snapshot = create_backup(settings)

    assert snapshot.exists()
    assert snapshot.parent == data_dir / "backups"
    # Los datos son recuperables del snapshot
    conn = sqlite3.connect(snapshot)
    val = conn.execute("SELECT id FROM t").fetchone()[0]
    conn.close()
    assert val == 42


def test_retencion_purga_antiguos(tmp_path):
    """§23.3.6: conserva los N más recientes, purga los antiguos (best-effort T14)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _make_db(data_dir)
    settings = Settings(_env_file=None, data_dir=data_dir, system_backup_retain_count=2)

    # Crear 3 snapshots
    for _ in range(3):
        create_backup(settings)

    backups = sorted((data_dir / "backups").glob("*.db"))
    assert len(backups) == 2  # solo los 2 más recientes


def test_error_limpio_operational(tmp_path, monkeypatch):
    """OperationalError → error limpio (nunca traceback)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _make_db(data_dir)
    settings = Settings(_env_file=None, data_dir=data_dir)

    # Simular fallo de VACUUM INTO
    import tikdown_rs.services.backup as backup_mod

    def _boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(backup_mod.sqlite3, "connect", _boom)
    with pytest.raises(sqlite3.OperationalError):
        create_backup(settings)

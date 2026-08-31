"""e01s04 — Migraciones idempotentes (T29/T68/T70)."""

# story: e01s04
import pytest

from tikdown_rs.core.migrations import _find_alembic_ini, apply_migrations


def test_find_alembic_ini_por_candidatos():
    """T70: localiza alembic.ini por candidatos (junto al módulo o cwd)."""
    ini = _find_alembic_ini()
    assert ini.name == "alembic.ini"
    assert ini.exists()


def test_find_alembic_ini_error_si_ninguno(tmp_path, monkeypatch):
    """T70: error accionable si no existe alembic.ini."""

    monkeypatch.chdir(tmp_path)  # cwd sin alembic.ini
    # Simular que el módulo está en site-packages sin alembic.ini cerca
    import tikdown_rs.core.migrations as mig

    monkeypatch.setattr(mig, "__file__", str(tmp_path / "migrations.py"))
    with pytest.raises(FileNotFoundError):
        mig._find_alembic_ini()


def test_alembic_single_head():
    """Auditoría ronda 5 (5.1): dos heads rompen `alembic upgrade head` y el arranque del daemon."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(_find_alembic_ini()))
    heads = script.get_heads()
    assert len(heads) == 1, (
        f"Alembic tiene {len(heads)} heads {heads}: "
        "consolida en uno antes de mergear"
    )


def test_apply_migrations_idempotente(tmp_path):
    """T29/T68: apply_migrations aplica y es reaplicable (idempotente)."""
    db_file = tmp_path / "data" / "test.db"
    url = f"sqlite+aiosqlite:///{db_file}"
    apply_migrations(url)  # primera vez
    apply_migrations(url)  # idempotente
    import sqlite3

    conn = sqlite3.connect(db_file)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    conn.close()
    assert "monitored_accounts" in tables
    assert "daemon_state" in tables


async def test_apply_migrations_desde_loop_async(tmp_path):
    """T51/T68: apply_migrations es seguro llamada desde un event loop async (daemon)."""
    import asyncio

    db_file = tmp_path / "async" / "test.db"
    url = f"sqlite+aiosqlite:///{db_file}"
    await asyncio.to_thread(apply_migrations, url)
    import sqlite3

    conn = sqlite3.connect(db_file)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    conn.close()
    assert "monitored_accounts" in tables

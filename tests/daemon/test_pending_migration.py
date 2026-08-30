"""e13s01-r3 — migración pending_status + descarga automática del monitor.

Ronda 3 (2.1-bis): el CHECK ck_videos_status no admitía 'pending' → el monitor
fallaba silenciosamente al descubrir contenido nuevo. Test de migración sobre
el camino real: schema previo → 'pending' rechazado → migrar → OK.
Ronda 3 (2.1-ter): el monitor debe DESCARGAR lo que descubre (fake engine).
"""

# story: e03s02
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.models.models import Base, MonitoredAccount, Video

_REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _create_old_schema_sync(conn):
    """Tabla videos con el CHECK VIEJO (como dejaría la migración e13s01)."""
    conn.execute(
        """
        CREATE TABLE videos (
            id INTEGER NOT NULL,
            tiktok_video_id VARCHAR(64) NOT NULL,
            account_id INTEGER,
            url TEXT,
            title TEXT,
            description TEXT,
            duration INTEGER,
            upload_date VARCHAR(8),
            local_path TEXT,
            file_size BIGINT,
            file_hash VARCHAR(64),
            status VARCHAR(16) NOT NULL,
            downloaded_at VARCHAR(32),
            retry_count INTEGER NOT NULL,
            error_message TEXT,
            error_category VARCHAR(16),
            created_at VARCHAR(32) NOT NULL,
            updated_at VARCHAR(32) NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_videos_status CHECK (status IN
                ('downloaded','failed','cancelled','skipped')),
            CONSTRAINT ck_videos_error_category CHECK (error_category IN
                ('definitive','transient','integrity')),
            UNIQUE (tiktok_video_id),
            FOREIGN KEY(account_id) REFERENCES monitored_accounts (id)
        )
        """
    )


def _alembic_cfg(tmp_path):
    from alembic.config import Config

    cfg = Config(str(_REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO / "alembic"))
    # env.py async (T51): exige driver aiosqlite
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{tmp_path}/tikdown.db")
    return cfg


def test_migracion_pending_status(tmp_path):
    """Old schema: 'pending' rechazado → migración → 'pending' aceptado."""
    from alembic import command

    db_path = tmp_path / "tikdown.db"

    # 1. Schema viejo post-e13s01: monitored_accounts (FK) + videos con CHECK
    # viejo + fila downloaded + stamp de la revisión previa
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE monitored_accounts (
            id INTEGER NOT NULL PRIMARY KEY,
            username VARCHAR(255) NOT NULL UNIQUE,
            mode VARCHAR(16) NOT NULL,
            paused BOOLEAN NOT NULL,
            needs_review BOOLEAN NOT NULL,
            notify_on_download BOOLEAN NOT NULL,
            monitor_after_backfill BOOLEAN NOT NULL,
            backfill_status VARCHAR(16) NOT NULL,
            backfill_pause_reason VARCHAR(32),
            backfill_cursor VARCHAR(255),
            backfill_total INTEGER NOT NULL,
            backfill_done INTEGER NOT NULL,
            last_check_at VARCHAR(32),
            follower_count INTEGER,
            following_count INTEGER,
            total_likes INTEGER,
            video_count INTEGER,
            profile_last_refreshed VARCHAR(32),
            created_at VARCHAR(32) NOT NULL,
            updated_at VARCHAR(32) NOT NULL
        )
        """
    )
    _create_old_schema_sync(conn)
    conn.execute(
        "INSERT INTO monitored_accounts (username, mode, paused, needs_review,"
        " notify_on_download, monitor_after_backfill, backfill_status,"
        " backfill_total, backfill_done, created_at, updated_at)"
        " VALUES ('obs', 'monitor', 0, 0, 0, 0, 'idle', 0, 0, '2026-08-30', '2026-08-30')"
    )
    conn.execute(
        "INSERT INTO videos (tiktok_video_id, status, retry_count, created_at, updated_at)"
        " VALUES ('viejo', 'downloaded', 0, '2026-08-30', '2026-08-30')"
    )
    conn.commit()
    conn.close()

    # 2. 'pending' rechazado con el schema viejo (el bug original)
    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError, match="ck_videos_status"):
        conn.execute(
            "INSERT INTO videos (tiktok_video_id, status, retry_count, created_at, updated_at)"
            " VALUES ('nuevo', 'pending', 0, '2026-08-30', '2026-08-30')"
        )
    conn.close()

    # 3. Migrar (stamp previo + upgrade hasta nuestra revisión)
    cfg = _alembic_cfg(tmp_path)
    command.stamp(cfg, "e13s01_backfill_slot")
    command.upgrade(cfg, "a1b2c3d4e5f6_pending_status")

    # 4. 'pending' ahora VÁLIDO; la fila vieja intacta
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO videos (tiktok_video_id, status, retry_count, created_at, updated_at)"
        " VALUES ('nuevo', 'pending', 0, '2026-08-30', '2026-08-30')"
    )
    conn.commit()
    rows = conn.execute("SELECT tiktok_video_id, status FROM videos").fetchall()
    conn.close()
    assert ("viejo", "downloaded") in rows
    assert ("nuevo", "pending") in rows


async def test_model_metadata_incluye_pending():
    """El modelo (fuente de verdad) declara 'pending' en el CHECK — el schema
    que crea Base.metadata.create_all (tests) coincide con la migración."""
    from sqlalchemy import create_engine

    from tikdown_rs.models.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.connect() as c:
        ddl = c.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='videos'"
        ).scalar_one()
    assert "'pending'" in ddl and "ck_videos_status" in ddl


async def test_monitor_job_download_pendings(maker, tmp_path):
    """2.1-ter: el monitor DESCARGA lo descubierto (mismo motor que backfill,
    pacing T62) — descubre → persiste pending → descarga → marca downloaded."""
    from tikdown_rs.core.config import Settings
    from tikdown_rs.daemon.monitor_job import MonitorJob

    class FakeEngine:
        def __init__(self):
            self.downloaded: list[str] = []

        async def extract_profile(self, username: str) -> dict:
            return {"entries": [{"id": "7401", "title": "v", "upload_date": "20260830"}]}

        async def download(self, url: str, archive_path=None, **kw) -> dict:
            self.downloaded.append(url)
            return {
                "info": {"id": "7401", "formats": [{"ext": "mp4"}]},
                "target": None,
            }

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    m = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(_env_file=None, data_dir=tmp_path)
    fake = FakeEngine()

    try:
        job = MonitorJob(m, settings=settings, engine=fake)
        async with m() as s:
            s.add(MonitoredAccount(username="obs", mode="monitor"))
            await s.commit()

        # 1 ciclo completo de discover (crea Video pending)
        async with m() as s:
            acct = (
                await s.execute(select(MonitoredAccount).where(MonitoredAccount.username == "obs"))
            ).scalar_one()
            await job.daemon_discover(s, acct)

        # 2. download_pendings: descarga y marca
        async with m() as s:
            await job.download_pendings(s)
        assert fake.downloaded == ["https://www.tiktok.com/@obs/video/7401"]
        async with m() as s:
            row = (
                await s.execute(select(Video).where(Video.tiktok_video_id == "7401"))
            ).scalar_one()
            assert row.status == "downloaded"
            assert row.downloaded_at is not None
            assert row.url is not None

        # 3. Sin pendientes: no reintenta ni re-descarga
        async with m() as s:
            await job.download_pendings(s)
        assert fake.downloaded.count("https://www.tiktok.com/@obs/video/7401") == 1
    finally:
        await engine.dispose()

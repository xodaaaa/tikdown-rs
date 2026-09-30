"""Model tests against a REAL migrated file database (never mocks).

Trampas covered: T-DATA-1, T-BACKFILL-9, T-DB-7, T-DB-12. Reglas: 3.1-3.6, 3.7, 13.2.
"""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.models import (
    BackfillSlot,
    Cookie,
    DownloadPacingState,
    MonitoredAccount,
    Video,
    acquire_backfill_slot,
    release_backfill_slot,
    reserve_download_slot,
)

LEGAL_VIDEO_STATUSES = ["pending", "downloaded", "failed", "cancelled", "skipped"]
LEGAL_BACKFILL_STATUSES = [
    "idle",
    "queued",
    "backfilling",
    "paused",
    "completed",
    "failed",
    "cancelled",
]
LEGAL_VALIDATION_STATES = ["valid", "invalid", "inconclusive"]


@pytest.fixture
async def migrated_factory(tmp_path: Path):
    """Engine + session factory over a REAL file DB migrated with run_migrations.

    run_migrations drives alembic's async env.py, which calls asyncio.run(); it
    must execute outside this test's running loop, hence the worker thread.
    """
    await asyncio.to_thread(run_migrations, tmp_path)
    engine = create_db_engine(sqlite_url_for(tmp_path))
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


async def add_account(factory, **overrides) -> int:
    """Insert one monitored_account row and return its id."""
    params = {"username": "default_acct", "backfill_status": "idle"}
    params.update(overrides)
    async with factory() as session:
        result = await session.execute(
            text(
                "INSERT INTO monitored_accounts (username, backfill_status) "
                "VALUES (:username, :backfill_status) RETURNING id"
            ),
            params,
        )
        account_id = result.scalar_one()
        await session.commit()
    return account_id


# --- T-DATA-1: videos.status CHECK exercised against the real migrated database ---


@pytest.mark.parametrize("status", LEGAL_VIDEO_STATUSES)
async def test_video_accepts_every_legal_status(migrated_factory, status: str) -> None:
    account_id = await add_account(migrated_factory)
    async with migrated_factory() as session:
        session.add(Video(tiktok_video_id=f"vid_{status}", account_id=account_id, status=status))
        await session.commit()


async def test_video_rejects_illegal_status(migrated_factory) -> None:
    """'integrity' is an error_category, never a status (3.3): the CHECK must reject it."""
    account_id = await add_account(migrated_factory)
    async with migrated_factory() as session:
        session.add(Video(tiktok_video_id="vid_bad", account_id=account_id, status="integrity"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_video_error_category_check(migrated_factory) -> None:
    account_id = await add_account(migrated_factory)
    for category in ["definitive", "transient", "integrity"]:
        async with migrated_factory() as session:
            session.add(
                Video(
                    tiktok_video_id=f"cat_{category}",
                    account_id=account_id,
                    status="failed",
                    error_category=category,
                )
            )
            await session.commit()
    async with migrated_factory() as session:
        session.add(
            Video(
                tiktok_video_id="cat_bad",
                account_id=account_id,
                status="failed",
                error_category="fatal",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


# --- T-BACKFILL-9: backfill_status enum complete from the first schema ---


@pytest.mark.parametrize("backfill_status", LEGAL_BACKFILL_STATUSES)
async def test_account_accepts_every_backfill_status(
    migrated_factory, backfill_status: str
) -> None:
    async with migrated_factory() as session:
        session.add(
            MonitoredAccount(username=f"u_{backfill_status}", backfill_status=backfill_status)
        )
        await session.commit()


async def test_account_rejects_illegal_backfill_status(migrated_factory) -> None:
    async with migrated_factory() as session:
        session.add(MonitoredAccount(username="u_bad", backfill_status="resuming"))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.parametrize("pause_reason", [None, "disk", "network"])
async def test_paused_account_accepts_pause_reasons(
    migrated_factory, pause_reason: str | None
) -> None:
    async with migrated_factory() as session:
        session.add(
            MonitoredAccount(
                username=f"p_{pause_reason}",
                backfill_status="paused",
                backfill_pause_reason=pause_reason,
            )
        )
        await session.commit()


async def test_paused_account_rejects_unknown_pause_reason(migrated_factory) -> None:
    async with migrated_factory() as session:
        session.add(
            MonitoredAccount(
                username="p_bad",
                backfill_status="paused",
                backfill_pause_reason="other",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_account_rejects_illegal_mode(migrated_factory) -> None:
    async with migrated_factory() as session:
        session.add(MonitoredAccount(username="m_bad", mode="hybrid"))
        with pytest.raises(IntegrityError):
            await session.commit()


# --- 3.4 cookies ---


@pytest.mark.parametrize("validation_state", LEGAL_VALIDATION_STATES)
async def test_cookie_accepts_every_validation_state(
    migrated_factory, validation_state: str
) -> None:
    async with migrated_factory() as session:
        session.add(
            Cookie(
                label="main",
                cookie_blob=b"# Netscape HTTP Cookie File",
                validation_state=validation_state,
            )
        )
        await session.commit()


async def test_cookie_blob_is_binary_and_roundtrips(migrated_factory) -> None:
    async with migrated_factory() as session:
        session.add(
            Cookie(
                label="bin", cookie_blob=b"\x00\x01\xff raw bytes", validation_state="inconclusive"
            )
        )
        await session.commit()
    async with migrated_factory() as session:
        row = await session.get(Cookie, 1)
    assert row is not None
    assert row.cookie_blob == b"\x00\x01\xff raw bytes"


async def test_cookie_blob_is_not_nullable(migrated_factory) -> None:
    async with migrated_factory() as session:
        session.add(Cookie(label="missing", cookie_blob=None))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_cookie_rejects_illegal_validation_state(migrated_factory) -> None:
    async with migrated_factory() as session:
        session.add(Cookie(label="bad", cookie_blob=b"x", validation_state="expired"))
        with pytest.raises(IntegrityError):
            await session.commit()


# --- 3.5 singleton tables: CHECK (id = 1) ---


@pytest.mark.parametrize("model", [BackfillSlot, DownloadPacingState])
async def test_singleton_tables_reject_second_row(migrated_factory, model) -> None:
    async with migrated_factory() as session:
        session.add(model(id=2))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_daemon_state_singleton_rejects_second_row(migrated_factory) -> None:
    from tikdown_rs.models import DaemonState

    async with migrated_factory() as session:
        session.add(DaemonState(id=2))
        with pytest.raises(IntegrityError):
            await session.commit()


# --- Migration is pure DDL: no singleton rows pre-inserted ---


async def test_migrations_insert_no_singleton_rows(migrated_factory) -> None:
    async with migrated_factory() as session:
        backfill = (await session.execute(text("SELECT COUNT(*) FROM backfill_slot"))).scalar()
        pacing = (
            await session.execute(text("SELECT COUNT(*) FROM download_pacing_state"))
        ).scalar()
    assert backfill == 0
    assert pacing == 0


# --- 3.5 backfill_slot CAS acquisition (cross-process semantics, single process here) ---


async def test_cas_slot_first_acquire_wins_second_gets_none(migrated_factory) -> None:
    async with migrated_factory() as session:
        assert await acquire_backfill_slot(session, "daemon") == "daemon"
        assert await acquire_backfill_slot(session, "cli") is None


async def test_cas_slot_release_then_reacquire(migrated_factory) -> None:
    async with migrated_factory() as session:
        assert await acquire_backfill_slot(session, "daemon") == "daemon"
        await release_backfill_slot(session, "daemon")
        assert await acquire_backfill_slot(session, "bot") == "bot"


async def test_cas_slot_release_by_non_owner_is_noop(migrated_factory) -> None:
    async with migrated_factory() as session:
        assert await acquire_backfill_slot(session, "daemon") == "daemon"
        await release_backfill_slot(session, "cli")
        assert await acquire_backfill_slot(session, "cli") is None


# --- 3.5 download_pacing_state atomic reservation (T-DB-7: milliseconds timespec) ---


async def test_pacing_reservation_wins_then_blocks_until_expiry(migrated_factory) -> None:
    async with migrated_factory() as session:
        assert await reserve_download_slot(
            session,
            now="2026-01-01T00:00:00.000+00:00",
            next_allowed="2026-01-01T00:00:05.000+00:00",
        )
        # Within the reservation window the slot is denied.
        assert not await reserve_download_slot(
            session,
            now="2026-01-01T00:00:03.000+00:00",
            next_allowed="2026-01-01T00:00:08.000+00:00",
        )
        # After the window expires a new reservation wins.
        assert await reserve_download_slot(
            session,
            now="2026-01-01T00:00:05.000+00:00",
            next_allowed="2026-01-01T00:00:10.000+00:00",
        )
    async with migrated_factory() as session:
        row = await session.get(DownloadPacingState, 1)
    assert row is not None
    assert row.next_allowed_at == "2026-01-01T00:00:10.000+00:00"


async def test_pacing_reservation_persists_across_sessions(migrated_factory) -> None:
    """T-DB-13: the helper commits internally; a second session sees the state."""
    async with migrated_factory() as session:
        assert await reserve_download_slot(
            session,
            now="2026-01-01T00:00:00.000+00:00",
            next_allowed="2026-01-01T00:00:05.000+00:00",
        )
    async with migrated_factory() as session:
        assert not await reserve_download_slot(
            session,
            now="2026-01-01T00:00:01.000+00:00",
            next_allowed="2026-01-01T00:00:06.000+00:00",
        )

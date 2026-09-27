"""services/integrity.check_integrity: read-only archive diagnostic (M6 T4).

Plan §14.5: `videos integrity [usuario]` = "tamaño + SHA-256 + ffprobe", and
the backup/restore paragraph says to re-run it after restoring. Read-only
diagnostic: no persisted state is modified (the plan assigns no write to it).

All ffprobe and binary detection doubles are deterministic: NO test ever
invokes a real ffprobe binary or a real shutil.which.
"""

import asyncio
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from typer.testing import CliRunner

from tikdown_rs.cli.main import app as cli_app
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.models import MonitoredAccount, Video
from tikdown_rs.services.integrity import IntegrityReport, check_integrity

PAYLOAD = b"integrity probe payload for tikdown-rs"
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()

cli_runner = CliRunner()


@pytest.fixture
async def migrated_factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Session factory over a REAL migrated file database (T-DATA-1 pattern)."""
    await asyncio.to_thread(run_migrations, tmp_path)
    engine = create_db_engine(sqlite_url_for(tmp_path))
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


async def add_account(factory, username: str = "acct") -> int:
    async with factory() as session:
        account = MonitoredAccount(username=username, backfill_status="idle")
        session.add(account)
        await session.commit()
        return account.id


async def seed_video(
    factory, account_id: int, video_id: str, *, local_path: str | None = None, **fields
) -> int:
    async with factory() as session:
        row = Video(
            tiktok_video_id=video_id,
            account_id=account_id,
            status="downloaded",
            **fields,
        )
        if local_path is not None:
            row.local_path = local_path
        session.add(row)
        await session.commit()
        return row.id


def write_video(tmp_path: Path, name: str) -> Path:
    """Create a known-payload video file under tmp_path."""
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PAYLOAD)
    return path


def ffprobe_ok(path: Path) -> bool:
    return True


def ffprobe_fail(path: Path) -> bool:
    return False


def which_none(name: str):
    """ffprobe detection double: binary never found."""
    return


def which_found(name: str):
    """ffprobe detection double: binary found (the runner fn is injected)."""
    return "/fake/ffprobe"


# --- streamed SHA-256 correctness ---


async def test_sha256_is_streamed_and_correct(tmp_path: Path, migrated_factory) -> None:
    account_id = await add_account(migrated_factory)
    video_file = write_video(tmp_path, "videos/1.mp4")
    await seed_video(migrated_factory, account_id, "1", local_path=str(video_file))

    report = await check_integrity(migrated_factory, ffprobe_path_fn=which_none)

    row = report.rows[0]
    assert row.sha256 == PAYLOAD_SHA256
    assert row.size == len(PAYLOAD)
    assert row.missing is False


# --- step 1: missing / zero-size files ---


async def test_missing_file_is_flagged_missing(tmp_path: Path, migrated_factory) -> None:
    account_id = await add_account(migrated_factory)
    await seed_video(migrated_factory, account_id, "1", local_path=str(tmp_path / "gone.mp4"))

    report = await check_integrity(migrated_factory, ffprobe_path_fn=which_none)

    row = report.rows[0]
    assert row.missing is True
    assert row.sha256 is None
    assert report.missing == 1
    assert report.ok == 0


async def test_zero_size_file_is_flagged_missing(tmp_path: Path, migrated_factory) -> None:
    account_id = await add_account(migrated_factory)
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")  # zero bytes
    await seed_video(migrated_factory, account_id, "1", local_path=str(empty))

    report = await check_integrity(migrated_factory, ffprobe_path_fn=which_none)

    assert report.rows[0].missing is True
    assert report.missing == 1


# --- ffprobe availability (detection reuse, skip = WARNING not failure) ---


async def test_ffprobe_unavailable_skips_container_check_with_marker(
    tmp_path: Path, migrated_factory
) -> None:
    account_id = await add_account(migrated_factory)
    video_file = write_video(tmp_path, "videos/1.mp4")
    await seed_video(migrated_factory, account_id, "1", local_path=str(video_file))

    report = await check_integrity(migrated_factory, ffprobe_path_fn=which_none)

    row = report.rows[0]
    assert row.ffprobe_skipped is True
    assert row.container_ok is None  # inconclusive, not failed
    assert report.ffprobe_skipped == 1
    assert report.container_failed == 0


async def test_ffprobe_available_exit_0_is_container_ok(tmp_path: Path, migrated_factory) -> None:
    account_id = await add_account(migrated_factory)
    video_file = write_video(tmp_path, "videos/1.mp4")
    await seed_video(migrated_factory, account_id, "1", local_path=str(video_file))

    report = await check_integrity(
        migrated_factory, ffprobe_path_fn=which_found, ffprobe_fn=ffprobe_ok
    )

    row = report.rows[0]
    assert row.ffprobe_skipped is False
    assert row.container_ok is True
    assert report.ok == 1


async def test_ffprobe_available_exit_1_fails_container(tmp_path: Path, migrated_factory) -> None:
    account_id = await add_account(migrated_factory)
    video_file = write_video(tmp_path, "videos/1.mp4")
    await seed_video(migrated_factory, account_id, "1", local_path=str(video_file))

    report = await check_integrity(
        migrated_factory, ffprobe_path_fn=which_found, ffprobe_fn=ffprobe_fail
    )

    row = report.rows[0]
    assert row.container_ok is False
    assert report.container_failed == 1
    assert report.ok == 0


# --- username filter ---


async def test_username_filter_restricts_rows(tmp_path: Path, migrated_factory) -> None:
    first = await add_account(migrated_factory, "acct")
    second = await add_account(migrated_factory, "other")
    for account_id, video_id in ((first, "1"), (second, "2")):
        video_file = write_video(tmp_path, f"{video_id}.mp4")
        await seed_video(migrated_factory, account_id, video_id, local_path=str(video_file))

    report = await check_integrity(migrated_factory, username="@Acct", ffprobe_path_fn=which_none)

    assert [row.tiktok_video_id for row in report.rows] == ["1"]
    assert report.total_checked == 1


# --- empty archive ---


async def test_empty_archive_gives_empty_summary(migrated_factory) -> None:
    report = await check_integrity(migrated_factory, ffprobe_path_fn=which_none)

    assert report.total_checked == 0
    assert report.rows == []
    assert report.ok == 0
    assert report.missing == 0


# --- report shape: plain JSON-safe types ---


async def test_report_json_round_trips_plain_types(tmp_path: Path, migrated_factory) -> None:
    account_id = await add_account(migrated_factory)
    video_file = write_video(tmp_path, "videos/1.mp4")
    await seed_video(migrated_factory, account_id, "1", local_path=str(video_file))
    await seed_video(migrated_factory, account_id, "2", local_path=str(tmp_path / "gone.mp4"))

    report = await check_integrity(migrated_factory, ffprobe_path_fn=which_none)

    assert isinstance(report, IntegrityReport)
    restored = json.loads(json.dumps(asdict(report)))
    assert restored["total_checked"] == 2
    assert restored["missing"] == 1
    assert restored["rows"][0]["sha256"] == PAYLOAD_SHA256


# --- read-only diagnostic: persisted state untouched ---


async def test_check_does_not_modify_video_rows(tmp_path: Path, migrated_factory) -> None:
    account_id = await add_account(migrated_factory)
    await seed_video(migrated_factory, account_id, "1", local_path=str(tmp_path / "gone.mp4"))

    await check_integrity(migrated_factory, ffprobe_path_fn=which_none)

    async with migrated_factory() as session:
        video = await session.get(Video, 1)
    assert video.status == "downloaded"  # diagnostic never rewrites state
    assert video.local_path == str(tmp_path / "gone.mp4")


# --- CLI: videos integrity [username] ---


def _seed_cli_db(tmp_path: Path, specs: list[dict]) -> None:
    run_migrations(tmp_path)

    async def seed() -> None:
        engine = create_db_engine(sqlite_url_for(tmp_path))
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                account = MonitoredAccount(username="acct", backfill_status="idle")
                session.add(account)
                await session.flush()
                for spec in specs:
                    session.add(Video(account_id=account.id, **spec))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())


def _cli_with_no_ffprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic CLI tests: ffprobe is never really present (T-CLI-1)."""
    import tikdown_rs.services.integrity as integrity_module

    monkeypatch.setattr(integrity_module.shutil, "which", which_none)


def test_cli_integrity_happy_path_reports_summary_and_non_ok_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _cli_with_no_ffprobe(monkeypatch)
    _seed_cli_db(
        tmp_path,
        [
            {
                "tiktok_video_id": "1",
                "status": "downloaded",
                "local_path": str(tmp_path / "ok.mp4"),
                "file_size": len(PAYLOAD),
            },
            {
                "tiktok_video_id": "2",
                "status": "downloaded",
                "local_path": str(tmp_path / "gone.mp4"),
                "file_size": 10,
            },
        ],
    )
    (tmp_path / "ok.mp4").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "ok.mp4").write_bytes(PAYLOAD)

    result = cli_runner.invoke(cli_app, ["videos", "integrity"])

    assert result.exit_code == 0, result.output  # diagnostic: flagged != error
    assert "checked=2" in result.output
    assert "missing=1" in result.output
    assert "2" in result.output  # the non-ok row id is listed


def test_cli_integrity_username_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _cli_with_no_ffprobe(monkeypatch)
    _seed_cli_db(
        tmp_path,
        [
            {
                "tiktok_video_id": "1",
                "status": "downloaded",
                "local_path": str(tmp_path / "1.mp4"),
            }
        ],
    )

    result = cli_runner.invoke(cli_app, ["videos", "integrity", "@Acct"])

    assert result.exit_code == 0, result.output
    assert "checked=1" in result.output

"""DownloadArchive: text file source of truth + queryable mirror (3.6).

Trampas neutralizadas: T-DB-8, T-DEPLOY-15. Regla: 3.6.

The text file is yt-dlp's source of truth (append-only); the mirror table is
the queryable copy. The parser recognizes BOTH line formats ("tiktok <id>" and
bare "<id>", last token wins, T-DB-8) and the file reader skips a partial last
line without a trailing newline (T-DEPLOY-15). Deterministic: in-memory SQLite,
tmp_path files, no network.
"""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.archive import DownloadArchive
from tikdown_rs.core.db import create_db_engine, make_session_factory
from tikdown_rs.models import Base
from tikdown_rs.models.download_archive import DownloadArchive as DownloadArchiveRow
from tikdown_rs.models.monitored_account import MonitoredAccount

IN_MEMORY_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def session_factory() -> async_sessionmaker[AsyncSession]:
    """In-memory engine via core/db.py with the schema created (3.7)."""
    engine = create_db_engine(IN_MEMORY_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield make_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def archive_path(tmp_path: Path) -> Path:
    return tmp_path / "download_archive.txt"


@pytest.fixture
def archive(archive_path: Path, session_factory) -> DownloadArchive:
    return DownloadArchive(archive_path, session_factory)


class BrokenSessionFactory:
    """Factory whose sessions always fail (mirror insert/delete best-effort)."""

    def __call__(self):
        return self

    async def __aenter__(self):
        raise RuntimeError("db down")

    async def __aexit__(self, *exc: object) -> bool:
        return False


# --- parser (T-DB-8: ID is the LAST token; blank/comment -> None) ---


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("tiktok 123\n", "123"),
        ("123\n", "123"),
        ("tiktok  456 extra tokens\n", "tokens"),  # last token, whatever it is
        ("tiktok 789", "789"),
        ("\n", None),
        ("   \n", None),
        ("# comment\n", None),
    ],
)
def test_parse_line_last_token_rule(line: str, expected: str | None) -> None:
    assert DownloadArchive.parse_line(line) == expected


# --- add: file line FIRST (source of truth), mirror row second ---


async def test_add_appends_tiktok_line_and_mirrors(
    archive: DownloadArchive, archive_path: Path, session_factory
) -> None:
    # FK parent: download_archive.account_id references monitored_accounts (3.7 FKs ON).
    async with session_factory() as session:
        session.add(MonitoredAccount(id=7, username="user", backfill_status="idle"))
        await session.commit()
    await archive.add("123", account_id=7)
    assert archive_path.read_text(encoding="utf-8") == "tiktok 123\n"
    assert await archive.contains("123")
    async with session_factory() as session:
        row = await session.scalar(
            select(DownloadArchiveRow).where(DownloadArchiveRow.tiktok_id == "123")
        )
    assert row is not None and row.account_id == 7


async def test_add_keeps_file_entry_when_mirror_fails(
    archive_path: Path,
) -> None:
    broken = DownloadArchive(archive_path, BrokenSessionFactory())
    await broken.add("123")  # must NOT raise: the file entry survives
    assert archive_path.read_text(encoding="utf-8") == "tiktok 123\n"


# --- contains: mirror query; empty/missing file -> False ---


async def test_contains_false_on_missing_file(archive: DownloadArchive) -> None:
    assert not await archive.contains("999")


async def test_contains_false_on_empty_file(archive: DownloadArchive, archive_path: Path) -> None:
    archive_path.write_text("", encoding="utf-8")
    assert not await archive.contains("1")


# --- file reader: partial last line skipped (T-DEPLOY-15) ---


async def test_file_reader_skips_partial_last_line(
    archive: DownloadArchive, archive_path: Path
) -> None:
    archive_path.write_text("tiktok 1\ntiktok 2", encoding="utf-8")  # no trailing newline
    added = await archive.sync_from_file()
    assert added == 1
    assert await archive.contains("1")
    assert not await archive.contains("2")


# --- sync_from_file: rebuilds missing mirror rows, idempotent ---


async def test_sync_from_file_adds_missing_rows_once(
    archive: DownloadArchive, archive_path: Path
) -> None:
    archive_path.write_text("tiktok 1\ntiktok 2\n\n# kept comment\n", encoding="utf-8")
    assert await archive.sync_from_file() == 2
    assert await archive.sync_from_file() == 0  # idempotent
    assert await archive.contains("1") and await archive.contains("2")


async def test_sync_from_file_reconciles_with_preexisting_mirror_row(
    archive: DownloadArchive, archive_path: Path
) -> None:
    await archive.add("1")
    archive_path.write_text("tiktok 1\ntiktok 2\n", encoding="utf-8")
    assert await archive.sync_from_file() == 1
    assert await archive.contains("1") and await archive.contains("2")


# --- remove: discard before a fallback retry (4.5, T-ENGINE-18) ---


async def test_remove_discards_file_line_and_mirror_row(
    archive: DownloadArchive, archive_path: Path
) -> None:
    await archive.add("55")
    await archive.add("66")
    assert await archive.remove("55") is True
    assert archive_path.read_text(encoding="utf-8") == "tiktok 66\n"
    assert not await archive.contains("55")
    assert await archive.contains("66")


async def test_remove_missing_entry_is_noop(archive: DownloadArchive, archive_path: Path) -> None:
    assert await archive.remove("404") is False
    assert not archive_path.exists()

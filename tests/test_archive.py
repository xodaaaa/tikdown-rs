"""DownloadArchive: append-only text file, single source of truth (3.6).

Trampas neutralizadas: T-DB-8. Regla: 3.6.

The text file is yt-dlp's single source of truth (append-only); the mirror
table was removed (plan §3.6 amendment). The parser recognizes BOTH line
formats ("tiktok <id>" and bare "<id>", last token wins, T-DB-8).
Deterministic: tmp_path files, no network.
"""

from pathlib import Path

import pytest

from tikdown_rs.core.archive import DownloadArchive


@pytest.fixture
def archive_path(tmp_path: Path) -> Path:
    return tmp_path / "download_archive.txt"


@pytest.fixture
def archive(archive_path: Path) -> DownloadArchive:
    return DownloadArchive(archive_path)


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


# --- add: append to the file (single source of truth) ---


async def test_add_appends_tiktok_line(archive: DownloadArchive, archive_path: Path) -> None:
    await archive.add("123")
    assert archive_path.read_text(encoding="utf-8") == "tiktok 123\n"


async def test_add_creates_parent_dirs_and_accepts_account_id(
    tmp_path: Path,
) -> None:
    archive = DownloadArchive(tmp_path / "nested" / "download_archive.txt")
    await archive.add("123", account_id=7)
    assert (tmp_path / "nested" / "download_archive.txt").read_text(
        encoding="utf-8"
    ) == "tiktok 123\n"


# --- remove: discard before a fallback retry (4.5, T-ENGINE-18) ---


async def test_remove_discards_file_line(archive: DownloadArchive, archive_path: Path) -> None:
    await archive.add("55")
    await archive.add("66")
    assert await archive.remove("55") is True
    assert archive_path.read_text(encoding="utf-8") == "tiktok 66\n"


async def test_remove_missing_entry_is_noop(archive: DownloadArchive, archive_path: Path) -> None:
    assert await archive.remove("404") is False
    assert not archive_path.exists()
    assert list(archive_path.parent.iterdir()) == []  # T-DB-16: nothing created


async def test_remove_leaves_no_temp_files_and_file_stays_parseable(
    archive: DownloadArchive, archive_path: Path
) -> None:
    """T-DB-16: the rewrite stages an fsynced temp, then rewrites IN PLACE (no
    os.replace of a live-append file); the staged temp is removed on success
    and the source of truth stays parseable."""
    await archive.add("55")
    await archive.add("66")

    assert await archive.remove("55") is True

    assert archive_path.exists()
    assert archive_path.read_text(encoding="utf-8") == "tiktok 66\n"
    assert list(archive_path.parent.iterdir()) == [archive_path]  # no tmp-* leftovers


async def test_remove_crash_mid_write_recovers_from_staged_temp(
    archive: DownloadArchive, archive_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-DB-16 regression + R3-001: a crash between staging and the in-place
    write leaves the staged temp as the recovery marker, and the next
    DownloadArchive construction publishes it. No os.replace of a live-append
    file: yt-dlp's open O_APPEND descriptors are never stranded on a dead
    inode, and racing appends keep landing on the real archive."""
    await archive.add("55")
    await archive.add("66")

    real_open = Path.open

    def crashing_open(self: Path, mode: str = "r", *args: object, **kwargs: object):
        if "w" in mode and self == archive_path:
            raise RuntimeError("crash before in-place write")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", crashing_open)

    with pytest.raises(RuntimeError, match="crash before in-place write"):
        await archive.remove("55")
    monkeypatch.undo()

    # The staged temp survived as the recovery marker (kept content only).
    staged = archive_path.with_name(archive_path.name + ".tmp")
    assert staged.exists()
    assert staged.read_text(encoding="utf-8") == "tiktok 66\n"

    # A fresh construction publishes the staged content (crash recovery).
    DownloadArchive(archive_path)
    assert archive_path.read_text(encoding="utf-8") == "tiktok 66\n"
    assert not staged.exists()

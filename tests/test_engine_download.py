"""Engine download: format chain, archive both-calls rule, retry outtmpl, zombie timeout.

Trampas neutralizadas: T-ENGINE-10, T-ENGINE-18, T-ASYNC-8, T-ASYNC-14,
T-ASYNC-15, T-DEPLOY-20. Reglas: 4.2, 4.5.

The fake YoutubeDL replicates the real full signature (context manager +
``extract_info(url, download=True)``, T-DEPLOY-20) and captures the exact
options dict per attempt so the format chain and the archive path on BOTH
attempts (T-ENGINE-18) are asserted, never assumed. Deterministic: tmp_path,
in-memory SQLite, no network, no real yt-dlp invocation.
"""

import threading
from pathlib import Path
from typing import Self

import pytest
import yt_dlp
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.archive import DownloadArchive
from tikdown_rs.core.config import Settings
from tikdown_rs.core.db import create_db_engine, make_session_factory
from tikdown_rs.core.download_engine import YtDlpEngine
from tikdown_rs.core.download_engine.ytdlp_engine import DEFAULT_FORMAT, FALLBACK_FORMAT
from tikdown_rs.core.errors import DownloadTimeoutError
from tikdown_rs.core.paths import outtmpl_for
from tikdown_rs.models import Base

CANONICAL = "https://www.tiktok.com/@user/video/123"


class FakeYoutubeDL:
    """Full-signature double (T-DEPLOY-20) with a per-extraction behavior hook."""

    def __init__(self, options: dict, behavior, captured: list, filepath: Path) -> None:
        captured.append(dict(options))
        self._options = options
        self._behavior = behavior
        self._captured = captured
        self._filepath = filepath

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = True, **kwargs: object) -> dict:
        self._captured.append(("url", url, download))
        if self._behavior is not None:
            self._behavior(dict(self._options))
        return {"requested_downloads": [{"filepath": str(self._filepath)}]}


def patch_ydl(
    monkeypatch: pytest.MonkeyPatch,
    captured: list,
    behavior,
    filepath: Path,
) -> None:
    monkeypatch.setattr(
        yt_dlp, "YoutubeDL", lambda options: FakeYoutubeDL(options, behavior, captured, filepath)
    )


class FakeArchive:
    """Minimal archive surface for engine-only tests (T-ENGINE-18 path)."""

    def __init__(self, path: Path) -> None:
        self.archive_path = path
        self.removed: list[str] = []

    async def remove(self, video_id: str) -> bool:
        self.removed.append(video_id)
        return True


@pytest.fixture
async def session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_db_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield make_session_factory(engine)
    await engine.dispose()


def make_engine(tmp_path: Path, **settings_kwargs: object) -> YtDlpEngine:
    return YtDlpEngine(
        cookies_blob=b"# Netscape HTTP Cookie File\n",
        settings=Settings(data_dir=tmp_path, **settings_kwargs),  # type: ignore[arg-type]
    )


# --- success path ---


async def test_download_success_returns_requested_filepath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(tmp_path)
    filepath = tmp_path / "videos" / "user" / "123.mp4"
    captured: list = []
    patch_ydl(monkeypatch, captured, None, filepath)

    result = await engine.download(CANONICAL, "123", "user")

    assert result == filepath
    options = captured[0]
    # 4.2: DEFAULT_FORMAT progressive first (T-ENGINE-10), mp4 merge.
    assert options["format"] == DEFAULT_FORMAT
    assert options["merge_output_format"] == "mp4"
    assert options["outtmpl"] == outtmpl_for(tmp_path)
    assert ("url", CANONICAL, True) in captured


async def test_download_archive_path_passed_on_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(tmp_path)
    fake_archive = FakeArchive(tmp_path / "download_archive.txt")
    captured: list = []
    patch_ydl(monkeypatch, captured, None, tmp_path / "videos" / "user" / "123.mp4")

    await engine.download(CANONICAL, "123", "user", archive=fake_archive)

    assert captured[0]["download_archive"] == str(fake_archive.archive_path)
    assert fake_archive.removed == []  # success: nothing discarded


# --- outtmpl: uploader/id pattern + retry suffix (T-ASYNC-15) ---


async def test_retry_outtmpl_writes_retry_suffix_and_never_collides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(tmp_path)
    captured: list = []
    patch_ydl(monkeypatch, captured, None, tmp_path / "videos" / "user" / "123.mp4")

    await engine.download(CANONICAL, "123", "user", retry_index=2)

    outtmpl = captured[0]["outtmpl"]
    assert "%(uploader)s" in outtmpl
    assert "%(id)s.retry-2.%(ext)s" in outtmpl  # T-ASYNC-14/15: zombie can't collide


async def test_retry_zero_keeps_plain_outtmpl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(tmp_path)
    captured: list = []
    patch_ydl(monkeypatch, captured, None, tmp_path / "videos" / "user" / "123.mp4")

    await engine.download(CANONICAL, "123", "user", retry_index=0)

    assert ".retry-" not in captured[0]["outtmpl"]


# --- fallback chain (4.2 + T-ENGINE-18) ---


async def test_fallback_discards_archive_entry_before_second_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
) -> None:
    archive = DownloadArchive(tmp_path / "download_archive.txt", session_factory)
    await archive.add("123")
    formats_seen: list[str] = []
    archive_text_at_fallback: list[str] = []

    def behavior(options: dict) -> None:
        formats_seen.append(options["format"])
        if options["format"] == DEFAULT_FORMAT:
            raise yt_dlp.utils.DownloadError("Requested format is not available")
        # Snapshot DURING the fallback attempt: the entry must be gone already.
        archive_text_at_fallback.append(
            (tmp_path / "download_archive.txt").read_text(encoding="utf-8")
        )

    filepath = tmp_path / "videos" / "user" / "123.mp4"
    captured: list = []
    patch_ydl(monkeypatch, captured, behavior, filepath)
    engine = make_engine(tmp_path)

    result = await engine.download(CANONICAL, "123", "user", archive=archive)

    assert result == filepath
    assert formats_seen == [DEFAULT_FORMAT, FALLBACK_FORMAT]
    # T-ENGINE-18: discarded BEFORE attempt 2, or yt-dlp answers 'already downloaded'.
    assert archive_text_at_fallback == [""]
    assert not await archive.contains("123")
    # T-ENGINE-18 both-calls rule: the SAME archive file path on BOTH attempts.
    options_dicts = [item for item in captured if isinstance(item, dict)]
    assert len(options_dicts) == 2
    assert all(opts["download_archive"] == str(archive.archive_path) for opts in options_dicts)


async def test_format_override_runs_alone_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(tmp_path, download_format="best[height<=720]")
    formats_seen: list[str] = []

    def behavior(options: dict) -> None:
        formats_seen.append(options["format"])
        raise yt_dlp.utils.DownloadError("Requested format is not available")

    fake_archive = FakeArchive(tmp_path / "download_archive.txt")
    captured: list = []
    patch_ydl(monkeypatch, captured, behavior, tmp_path / "videos" / "user" / "123.mp4")

    with pytest.raises(yt_dlp.utils.DownloadError):
        await engine.download(CANONICAL, "123", "user", archive=fake_archive)

    # Operator decided the format: ONE attempt, no fallback, no archive discard.
    assert formats_seen == ["best[height<=720]"]
    assert fake_archive.removed == []
    assert captured[0]["download_archive"] == str(fake_archive.archive_path)


# --- timeout: zombie thread accounting (T-ASYNC-8/14/15) ---


async def test_timeout_raises_download_timeout_error_and_counts_zombie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unblock = threading.Event()
    formats_seen: list[str] = []

    def behavior(options: dict) -> None:
        formats_seen.append(options["format"])
        unblock.wait(timeout=10)  # simulates the unkillable native yt-dlp thread

    engine = make_engine(tmp_path, download_timeout_seconds=1)
    captured: list = []
    patch_ydl(monkeypatch, captured, behavior, tmp_path / "videos" / "user" / "123.mp4")

    try:
        with pytest.raises(DownloadTimeoutError, match="123"):
            await engine.download(CANONICAL, "123", "user")
    finally:
        unblock.set()  # release the zombie thread promptly

    assert engine.zombie_threads == 1  # T-ASYNC-14/15: counted for daemon status
    assert formats_seen == [DEFAULT_FORMAT]  # a timeout is NOT a format fallback


# --- T-ENGINE-32 (live round M6): adopt existing file on yt-dlp skip ---


class SkipYoutubeDL:
    """yt-dlp double that SKIPS: an existing final file yields an EMPTY
    requested_downloads ('has already been downloaded' path)."""

    def __init__(self, options: dict, captured: list) -> None:
        captured.append(dict(options))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = True, **kwargs: object) -> dict:
        return {}


def patch_skip_ydl(monkeypatch: pytest.MonkeyPatch, captured: list) -> None:
    monkeypatch.setattr(yt_dlp, "YoutubeDL", lambda options: SkipYoutubeDL(options, captured))


async def test_download_adopts_existing_file_when_ytdlp_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """retry-failed of a row whose file survived on disk must CONVERGE: yt-dlp
    skips (file exists), the engine adopts it by glob-by-id instead of failing
    (live round: 17 files on disk, every row churned failed/integrity)."""
    engine = make_engine(tmp_path)
    existing = tmp_path / "videos" / "user" / "123.mp4"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"real video bytes")
    captured: list = []
    patch_skip_ydl(monkeypatch, captured)

    result = await engine.download(CANONICAL, "123", "user")

    assert result == existing


async def test_download_adopt_prefers_final_over_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(tmp_path)
    final = tmp_path / "videos" / "user" / "123.mp4"
    retry = tmp_path / "videos" / "user" / "123.retry-1.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"final")
    retry.write_bytes(b"retry")
    captured: list = []
    patch_skip_ydl(monkeypatch, captured)

    result = await engine.download(CANONICAL, "123", "user")

    assert result == final


async def test_download_skip_without_file_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(tmp_path)
    captured: list = []
    patch_skip_ydl(monkeypatch, captured)

    with pytest.raises(RuntimeError, match="requested_downloads"):
        await engine.download(CANONICAL, "123", "user")


async def test_download_adopt_never_takes_part_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A .part file is an INCOMPLETE download: never adopted."""
    engine = make_engine(tmp_path)
    part = tmp_path / "videos" / "user" / "123.mp4.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"partial")
    captured: list = []
    patch_skip_ydl(monkeypatch, captured)

    with pytest.raises(RuntimeError, match="requested_downloads"):
        await engine.download(CANONICAL, "123", "user")

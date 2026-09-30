"""Feed listing rules (4.6): flat playlist, None filtering, URL normalization.

Trampas neutralizadas: T-ENGINE-11, T-ENGINE-12, T-ENGINE-13, T-ENGINE-14,
T-ENGINE-25, T-COOKIES-6, T-COOKIES-7, T-DEPLOY-20. Regla: 4.6.

The fake YoutubeDL replicates the real full signature (context manager +
``extract_info(url, download=False)``, T-DEPLOY-20) and captures the exact
options dict so every mandatory listing rule is asserted, never assumed.
"""

from pathlib import Path
from typing import Any, Self

import pytest
import yt_dlp

from tikdown_rs.core.config import Settings
from tikdown_rs.core.download_engine import YtDlpEngine

CANONICAL = "https://www.tiktok.com/@user/video/123"
CDN_URL = (
    "https://v16m-default.akamaized.net/video/tos/useast2a/123?/"
    "expires=999999999&signature=abc&" + "x" * 300
)
ENTRY: dict[str, Any] = {
    "id": "123",
    "url": CDN_URL,
    "title": "a video",
    "description": "desc",
    "duration": 30.5,
    "upload_date": "20260905",
    "uploader": "user",
}


class FakeYoutubeDL:
    """Full-signature double capturing constructor options and queried URLs."""

    def __init__(self, options: dict, captured: list, info: dict | None) -> None:
        captured.append(dict(options))
        self._captured = captured
        self._info = info
        self._options = options

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False, **kwargs: object) -> dict | None:
        self._captured.append(("url", url, download))
        # Snapshot the cookiefile DURING extraction: the engine deletes it in
        # finally (T-COOKIES-6), so it cannot be inspected after the call.
        self._captured.append(
            ("cookiefile-text", Path(self._options["cookiefile"]).read_text(encoding="utf-8"))
        )
        return self._info


def patch_ydl(monkeypatch: pytest.MonkeyPatch, calls: list, info: dict | None) -> None:
    monkeypatch.setattr(yt_dlp, "YoutubeDL", lambda options: FakeYoutubeDL(options, calls, info))


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch, info: dict | None) -> list:
    calls: list = []
    patch_ydl(monkeypatch, calls, info)
    return calls


@pytest.fixture
def info() -> dict | None:
    return {"entries": [None, dict(ENTRY), None]}  # T-ENGINE-13: failed entries are None


@pytest.fixture
def engine() -> YtDlpEngine:
    return YtDlpEngine(cookies_blob=b"# Netscape HTTP Cookie File\n", settings=Settings())


def captured_options(calls: list) -> dict:
    """The LAST captured options dict (later calls append new dicts)."""
    return [item for item in calls if isinstance(item, dict)][-1]


def captured_url(calls: list) -> str:
    return [item for item in calls if isinstance(item, tuple) and item[0] == "url"][-1][1]


def test_flat_playlist_and_ignoreerrors_always_on(engine: YtDlpEngine, captured: list) -> None:
    engine.list_videos("user")
    options = captured_options(captured)
    # T-ENGINE-12 (B2): extract_flat='in_playlist' ALWAYS — list URLs, never
    # resolve each video. 'flat_playlist' is a no-op key in yt-dlp (verified
    # against the pinned nightly: only 'extract_flat' is ever read).
    assert options["extract_flat"] == "in_playlist"
    assert "flat_playlist" not in options
    # T-ENGINE-13 companion: ignoreerrors=True so failed entries become None.
    assert options["ignoreerrors"] is True


def test_no_impersonate_parameter(engine: YtDlpEngine, captured: list) -> None:
    # T-ENGINE-11/T-ENGINE-15: impersonating the listing reduces it to 1 entry.
    engine.list_videos("user")
    assert "impersonate" not in captured_options(captured)


def test_cookiefile_written_from_blob_and_deleted_after(
    engine: YtDlpEngine, captured: list
) -> None:
    engine.list_videos("user")
    cookiefile = Path(captured_options(captured)["cookiefile"])
    assert cookiefile.name.startswith("tikdown-cookies-")
    tuples = [item for item in captured if isinstance(item, tuple)]
    cookie_text = [item for item in tuples if item[0] == "cookiefile-text"][-1][1]
    assert cookie_text.startswith("# Netscape HTTP Cookie File\n")
    # T-COOKIES-6: cleanup runs after extraction even on success.
    assert not cookiefile.exists()


def test_pacing_internals_present(engine: YtDlpEngine, captured: list) -> None:
    # 4.2: sleep_interval_requests 1-3 s (T-ENGINE-23), socket_timeout 20-30 s.
    engine.list_videos("user")
    options = captured_options(captured)
    assert 1 <= options["sleep_interval_requests"] <= 3
    assert 20 <= options["socket_timeout"] <= 30


def test_playlistend_honored(engine: YtDlpEngine, captured: list) -> None:
    engine.list_videos("user", max_entries=5)
    assert captured_options(captured)["playlistend"] == 5

    engine.list_videos("user")
    assert "playlistend" not in captured_options(captured)


def test_none_entries_filtered(engine: YtDlpEngine, captured: list) -> None:
    assert len(engine.list_videos("user")) == 1


def test_cdn_url_normalized_to_canonical_page_url(engine: YtDlpEngine, captured: list) -> None:
    # T-ENGINE-14: with cookies yt-dlp returns CDN URLs with giant query
    # strings; persisting them breaks %(id)s with OSError: File name too long.
    videos = engine.list_videos("user")
    assert videos[0]["url"] == CANONICAL


def test_username_at_sign_stripped(engine: YtDlpEngine, captured: list) -> None:
    engine.list_videos("@user")
    assert captured_url(captured) == "https://www.tiktok.com/@user"

    engine.list_videos("user")
    assert captured_url(captured) == "https://www.tiktok.com/@user"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20260905", "20260905"),  # already canonical
        ("2026-9-5", "20260905"),  # mixed-format variant, T-ENGINE-25
        ("2026-09-05", "20260905"),
        ("", ""),  # missing: caller assigns the previous updated cursor (T-BACKFILL-3)
        (None, ""),
        ("garbage", ""),
    ],
)
def test_upload_date_normalized_to_yyyymmdd(
    engine: YtDlpEngine,
    monkeypatch: pytest.MonkeyPatch,
    captured: list,
    raw: object,
    expected: str,
) -> None:
    variant_info = {"entries": [{**ENTRY, "upload_date": raw}]}
    patch_ydl(monkeypatch, captured, variant_info)
    videos = engine.list_videos("user")
    assert videos[0]["upload_date"] == expected


def test_flat_entry_timestamp_becomes_upload_date(
    engine: YtDlpEngine, monkeypatch: pytest.MonkeyPatch, captured: list
) -> None:
    """B2: a flat entry (extract_flat) carries 'timestamp' (createTime epoch,
    UTC) and NO 'upload_date' — the fallback converts it to the canonical
    YYYYMMDD (T-ENGINE-25) so the backfill cursor keeps its granularity."""
    from datetime import UTC, datetime

    day = datetime(2026, 9, 5, tzinfo=UTC)
    variant_info = {"entries": [{**ENTRY, "upload_date": None, "timestamp": int(day.timestamp())}]}
    patch_ydl(monkeypatch, captured, variant_info)
    videos = engine.list_videos("user")
    assert videos[0]["upload_date"] == "20260905"


def test_extract_profile_uses_flat_first_page(
    engine: YtDlpEngine, monkeypatch: pytest.MonkeyPatch, captured: list
) -> None:
    profile_info = {
        "entries": [dict(ENTRY)],
        "channel_follower_count": 4242,
        "playlist_count": 87,
    }
    patch_ydl(monkeypatch, captured, profile_info)
    profile = engine.extract_profile("user")
    options = captured_options(captured)
    assert options["extract_flat"] == "in_playlist"  # same flat listing rules, first page
    assert options["playlistend"] == 1
    assert profile["username"] == "user"
    assert profile["followers"] == 4242
    assert profile["video_count"] == 87


class TestHandleNormalizationLiveRound:
    """Live-round regression (M6, Apéndice A row): `accounts add` stores the
    profile URL verbatim; every engine consumer passes `account.username`
    straight into list_videos/extract_profile. A full URL fed into the
    f-string produced `https://www.tiktok.com/@https://www.tiktok.com/@user`,
    which TikTok redirects to /foryou — a SILENT empty listing (backfill
    completed total=0 on an account with 5 videos).
    """

    def test_list_videos_accepts_full_profile_url(
        self, engine: YtDlpEngine, captured: list
    ) -> None:
        engine.list_videos("https://www.tiktok.com/@rosary657")
        assert captured_url(captured) == "https://www.tiktok.com/@rosary657"

    def test_list_videos_accepts_url_with_query_and_trailing_slash(
        self, engine: YtDlpEngine, captured: list
    ) -> None:
        engine.list_videos("https://www.tiktok.com/@rosary657?lang=en/")
        assert captured_url(captured) == "https://www.tiktok.com/@rosary657"

    def test_list_videos_accepts_at_handle(self, engine: YtDlpEngine, captured: list) -> None:
        engine.list_videos("@rosary657")
        assert captured_url(captured) == "https://www.tiktok.com/@rosary657"

    def test_list_videos_accepts_bare_handle(self, engine: YtDlpEngine, captured: list) -> None:
        engine.list_videos("rosary657")
        assert captured_url(captured) == "https://www.tiktok.com/@rosary657"

    def test_canonical_video_urls_use_normalized_handle(
        self, engine: YtDlpEngine, captured: list
    ) -> None:
        videos = engine.list_videos("https://www.tiktok.com/@rosary657")
        assert videos and all(
            v["url"] == f"https://www.tiktok.com/@rosary657/video/{v['id']}" for v in videos
        )

    def test_extract_profile_accepts_full_profile_url(
        self, engine: YtDlpEngine, captured: list
    ) -> None:
        profile = engine.extract_profile("https://www.tiktok.com/@rosary657")
        assert captured_url(captured) == "https://www.tiktok.com/@rosary657"
        assert profile["username"] == "rosary657"

    @pytest.mark.parametrize("bad", ["https://www.tiktok.com", "   "])
    def test_unparseable_handle_raises(self, engine: YtDlpEngine, bad: str) -> None:
        with pytest.raises(ValueError, match="handle"):
            engine.list_videos(bad)

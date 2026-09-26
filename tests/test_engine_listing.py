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
    # T-ENGINE-12: flat_playlist=True ALWAYS, list URLs, never resolve each video.
    assert options["flat_playlist"] is True
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
    assert options["flat_playlist"] is True  # same flat listing rules, first page
    assert options["playlistend"] == 1
    assert profile["username"] == "user"
    assert profile["followers"] == 4242
    assert profile["video_count"] == 87


def test_validate_cookie_composes_probe_primitives() -> None:
    # 4.8 layering: core composes core.verify primitives; services/* stays
    # importable only from above. The injected probe_fn replaces yt-dlp here.
    calls: list[tuple[bytes, str, int]] = []

    def probe_fn(blob: bytes, url: str, max_entries: int) -> list[dict]:
        calls.append((blob, url, max_entries))
        if url == "first":
            raise RuntimeError("HTTP Error 403: Forbidden")  # transient: try next
        return [{"url": CANONICAL, "duration": 30.5}]

    settings = Settings(cookie_validation_url="first,second")
    powered = YtDlpEngine(cookies_blob=b"blob", settings=settings)
    assert powered.validate_cookie(probe_fn=probe_fn) == "valid"
    assert len(calls) == 2
    assert calls[0][1] == "first" and calls[1][1] == "second"


def test_validate_cookie_definitive_failure_is_invalid() -> None:
    def probe_fn(blob: bytes, url: str, max_entries: int) -> list[dict]:
        raise RuntimeError("this content is requiring login")

    settings = Settings(cookie_validation_url="first,second")
    powered = YtDlpEngine(cookies_blob=b"blob", settings=settings)
    assert powered.validate_cookie(probe_fn=probe_fn) == "invalid"


def test_validate_cookie_all_candidates_without_video_is_inconclusive() -> None:
    settings = Settings(cookie_validation_url="first,second")
    powered = YtDlpEngine(cookies_blob=b"blob", settings=settings)
    # T-COOKIES-2: slideshow-only feed (no video entries) on every candidate.
    assert (
        powered.validate_cookie(
            probe_fn=lambda blob, url, max_entries: [{"url": "/photo/", "duration": 0}]
        )
        == "inconclusive"
    )

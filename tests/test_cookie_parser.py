"""Cookie format detection, canonicalization and tempfile writing.

Trampas neutralizadas: T-COOKIES-1, T-COOKIES-6, T-COOKIES-7. Regla: 4.3, 7.
The REAL YoutubeDLCookieJar loads the regenerated tempfile (T-COOKIES-1).
"""

import json
import logging
from pathlib import Path

import pytest
from yt_dlp.cookies import YoutubeDLCookieJar

from tikdown_rs.core.cookie_parser import (
    detect_cookie_format,
    to_canonical_netscape,
    warn_missing_sid_tt,
    write_canonical_netscape_tempfile,
)

HEADER = "# Netscape HTTP Cookie File"

NETSCAPE_WITH_HEADER = (
    "# Netscape HTTP Cookie File\n.tiktok.com\tTRUE\t/\tTRUE\t1735689600\tsid_tt\tabc123\n"
)
NETSCAPE_WITHOUT_HEADER = ".tiktok.com\tTRUE\t/\tTRUE\t1735689600\tsid_tt\tabc123\n"
JSON_COOKIES = json.dumps(
    [
        {
            "name": "sid_tt",
            "value": "abc123",
            "domain": ".tiktok.com",
            "path": "/",
            "secure": True,
            "expires": 1735689600,
        }
    ]
)
COOKIE_STRING = "sid_tt=abc123; sessionid=xyz789"


# --- Format detection (7) ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (NETSCAPE_WITH_HEADER, "netscape"),
        (NETSCAPE_WITHOUT_HEADER, "netscape"),
        (JSON_COOKIES, "json"),
        (COOKIE_STRING, "cookie-string"),
    ],
)
def test_detect_cookie_format(text: str, expected: str) -> None:
    assert detect_cookie_format(text) == expected


# --- Canonicalization (4.3: header first line, never duplicated, \n semantics) ---


def test_canonical_adds_header_once_as_first_line() -> None:
    canonical = to_canonical_netscape(NETSCAPE_WITHOUT_HEADER)
    lines = canonical.splitlines()
    assert lines[0] == HEADER
    assert sum(1 for line in lines if line == HEADER) == 1


def test_canonical_does_not_duplicate_existing_header() -> None:
    canonical = to_canonical_netscape(NETSCAPE_WITH_HEADER)
    lines = canonical.splitlines()
    assert lines[0] == HEADER
    assert sum(1 for line in lines if line == HEADER) == 1


def test_canonical_from_json_keeps_value_with_spaces() -> None:
    text = json.dumps(
        [{"name": "note", "value": "hello world", "domain": ".tiktok.com", "path": "/"}]
    )
    canonical = to_canonical_netscape(text)
    fields = canonical.splitlines()[1].split("\t")
    assert len(fields) == 7
    assert fields[5] == "note"
    assert fields[6] == "hello world"


def test_canonical_is_unix_newlines_with_trailing_newline() -> None:
    canonical = to_canonical_netscape(JSON_COOKIES)
    assert canonical.endswith("\n")
    assert "\r" not in canonical


def test_cookie_string_round_trips_through_real_jar(tmp_path: Path) -> None:
    path = write_canonical_netscape_tempfile(COOKIE_STRING, dir=tmp_path)
    jar = YoutubeDLCookieJar(filename=str(path))
    jar.load()
    names = {cookie.name for cookie in jar}
    assert names == {"sid_tt", "sessionid"}


# --- T-COOKIES-1: the M1 acceptance case, loaded by the REAL parser ---


def test_json_via_tempfile_loads_in_real_ytdlp_jar(tmp_path: Path) -> None:
    path = write_canonical_netscape_tempfile(JSON_COOKIES, dir=tmp_path)
    raw = path.read_text(encoding="utf-8")
    assert raw.splitlines()[0] == HEADER
    assert sum(1 for line in raw.splitlines() if line == HEADER) == 1

    jar = YoutubeDLCookieJar(filename=str(path))
    jar.load()
    by_name = {cookie.name: cookie for cookie in jar}
    assert "sid_tt" in by_name
    assert by_name["sid_tt"].value == "abc123"
    assert by_name["sid_tt"].domain == ".tiktok.com"


def test_netscape_via_tempfile_loads_in_real_ytdlp_jar(tmp_path: Path) -> None:
    path = write_canonical_netscape_tempfile(NETSCAPE_WITH_HEADER, dir=tmp_path)
    jar = YoutubeDLCookieJar(filename=str(path))
    jar.load()
    assert {cookie.name for cookie in jar} == {"sid_tt"}


# --- T-COOKIES-7: fd closed immediately, file unlinkable right after return ---


def test_tempfile_can_be_unlinked_immediately(tmp_path: Path) -> None:
    path = write_canonical_netscape_tempfile(JSON_COOKIES, dir=tmp_path)
    path.unlink()  # On Windows this raises if the fd is still open.
    assert not path.exists()


# --- T-COOKIES-6: failed write leaves no orphan tempfile ---


def test_failed_write_leaves_no_tempfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self, *args, **kwargs):
        raise OSError("simulated mid-write failure")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(OSError, match="simulated mid-write failure"):
        write_canonical_netscape_tempfile(JSON_COOKIES, dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- sid_tt: warn, never reject (7) ---


def test_missing_sid_tt_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    text = json.dumps([{"name": "other", "value": "v", "domain": ".tiktok.com", "path": "/"}])
    with caplog.at_level(logging.WARNING, logger="tikdown_rs.core.cookie_parser"):
        missing = warn_missing_sid_tt(text)
    assert missing == ["sid_tt"]
    assert any("sid_tt" in record.message for record in caplog.records)


def test_present_sid_tt_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="tikdown_rs.core.cookie_parser"):
        missing = warn_missing_sid_tt(JSON_COOKIES)
    assert missing == []
    assert not [r for r in caplog.records if r.name == "tikdown_rs.core.cookie_parser"]

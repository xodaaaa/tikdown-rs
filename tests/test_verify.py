"""core.verify probe primitive: option surface, tempfile lifecycle, None filtering.

Trampas neutralizadas: T-ENGINE-11, T-ENGINE-12, T-ENGINE-13, T-DEPLOY-20,
T-COOKIES-1. Regla: 4.6, 7.

The yt_dlp double replicates the FULL real constructor signature (T-DEPLOY-20):
a trimmed fake would mask dead parameters.
"""

import sys
import types
from pathlib import Path

import yt_dlp

from tikdown_rs.core.cookie_parser import HEADER, to_canonical_netscape
from tikdown_rs.core.verify import probe_profile

PROBE_URL = "https://www.tiktok.com/@probe"
BLOB = to_canonical_netscape('[{"name": "sid_tt", "value": "abc", "domain": ".tiktok.com"}]')


class FakeYoutubeDL:
    """Full-signature double of yt_dlp.YoutubeDL (T-DEPLOY-20)."""

    last_instance: "FakeYoutubeDL | None" = None

    def __init__(self, params=None, auto_init=True, tokenizer=None):  # real signature
        self.params = params
        self.auto_init = auto_init
        self.tokenizer = tokenizer
        self.entered = False
        FakeYoutubeDL.last_instance = self

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def extract_info(self, url: str, download: bool):
        self.url = url
        self.download = download
        # T-COOKIES-1: read the tempfile while it still exists.
        self.cookiefile_first_line = (
            Path(self.params["cookiefile"]).read_text(encoding="utf-8").splitlines()[0]
        )
        return {"entries": [dict(FAKE_ENTRY), None, dict(FAKE_ENTRY2)]}


FAKE_ENTRY = {"url": "https://www.tiktok.com/@probe/video/1", "duration": 12.0}
FAKE_ENTRY2 = {"url": "https://www.tiktok.com/@probe/video/2", "duration": 30.0}


class _FakeImpersonationYDL:
    """Fake YoutubeDL for the impersonation probe (T-DEPLOY-20 signature)."""

    accessor_result: object = None
    accessor_raises: Exception | None = None

    def __init__(self, params=None, auto_init=True, tokenizer=None):  # real signature
        self.params = params

    def _get_available_impersonate_targets(self):
        if _FakeImpersonationYDL.accessor_raises is not None:
            raise _FakeImpersonationYDL.accessor_raises
        return _FakeImpersonationYDL.accessor_result


class _AccessorlessYDL:
    """Double where the private accessor does not exist (API removed upstream)."""

    def __init__(self, params=None, auto_init=True, tokenizer=None):  # real signature
        self.params = params


def test_probe_impersonation_layer1_tuples_normalized(monkeypatch) -> None:
    """Layer 1: (target, handler) tuples are normalized (T-ENGINE-6 shapes)."""
    from tikdown_rs.core.verify import probe_impersonation

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeImpersonationYDL, raising=True)
    _FakeImpersonationYDL.accessor_result = [(object(), "h1"), (object(), "h2")]
    _FakeImpersonationYDL.accessor_raises = None

    assert probe_impersonation() == (True, "private-api", 2)


def test_probe_impersonation_layer1_strings_normalized(monkeypatch) -> None:
    from tikdown_rs.core.verify import probe_impersonation

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeImpersonationYDL, raising=True)
    _FakeImpersonationYDL.accessor_result = ["chrome124", "safari131"]
    _FakeImpersonationYDL.accessor_raises = None

    assert probe_impersonation() == (True, "private-api", 2)


def test_probe_impersonation_layer1_missing_falls_to_layer2(monkeypatch) -> None:
    """No private accessor (API removed) is 'unavailable', not 'uninspectable'."""
    from tikdown_rs.core.verify import probe_impersonation

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _AccessorlessYDL, raising=True)
    monkeypatch.setitem(sys.modules, "curl_cffi", types.ModuleType("curl_cffi"))

    assert probe_impersonation() == (True, "curl_cffi-importable", 0)


def test_probe_impersonation_layer1_raises_falls_to_layer2(monkeypatch) -> None:
    from tikdown_rs.core.verify import probe_impersonation

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeImpersonationYDL, raising=True)
    _FakeImpersonationYDL.accessor_result = None
    _FakeImpersonationYDL.accessor_raises = RuntimeError("API shape changed")
    monkeypatch.setitem(sys.modules, "curl_cffi", types.ModuleType("curl_cffi"))

    assert probe_impersonation() == (True, "curl_cffi-importable", 0)


def test_probe_impersonation_curl_cffi_missing(monkeypatch) -> None:
    """Layer 2 blocked: unavailable with the curl_cffi-missing cause (T-ENGINE-22)."""
    from tikdown_rs.core.verify import probe_impersonation

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _AccessorlessYDL, raising=True)
    # None in sys.modules makes `import curl_cffi` raise ImportError.
    monkeypatch.setitem(sys.modules, "curl_cffi", None)

    assert probe_impersonation() == (False, "curl_cffi-missing", 0)


def test_probe_impersonation_garbage_never_raises(monkeypatch) -> None:
    """T-ENGINE-22: garbage from the private API is inspection-failed, never raised."""
    from tikdown_rs.core.verify import probe_impersonation

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeImpersonationYDL, raising=True)
    _FakeImpersonationYDL.accessor_result = 42  # not iterable: cannot inspect
    _FakeImpersonationYDL.accessor_raises = None
    assert probe_impersonation() == (False, "inspection-failed", 0)

    _FakeImpersonationYDL.accessor_result = None
    assert probe_impersonation() == (False, "inspection-failed", 0)


def test_probe_profile_option_surface_and_tempfile_lifecycle(monkeypatch) -> None:
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    FakeYoutubeDL.last_instance = None

    entries = probe_profile(BLOB.encode("utf-8"), PROBE_URL, max_entries=5)

    fake = FakeYoutubeDL.last_instance
    assert fake is not None and fake.entered
    opts = fake.params
    assert opts["flat_playlist"] is True  # T-ENGINE-12
    assert opts["ignoreerrors"] is True
    assert opts["playlistend"] == 5
    assert "impersonate" not in opts  # T-ENGINE-11
    cookiefile = Path(opts["cookiefile"])
    # T-COOKIES-1: the tempfile was canonical Netscape (header first line).
    assert fake.cookiefile_first_line == HEADER
    # tempfile deleted after the call (T-COOKIES-6)
    assert not cookiefile.exists()
    assert fake.url == PROBE_URL
    assert fake.download is False
    # T-ENGINE-13: None entries filtered
    assert entries == [dict(FAKE_ENTRY), dict(FAKE_ENTRY2)]


def test_probe_profile_cleans_up_tempfile_on_extractor_error(monkeypatch, tmp_path: Path) -> None:
    import pytest

    class ExplodingYoutubeDL(FakeYoutubeDL):
        def extract_info(self, url: str, download: bool):
            raise RuntimeError("boom")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", ExplodingYoutubeDL)
    with pytest.raises(RuntimeError, match="boom"):
        probe_profile(BLOB.encode("utf-8"), PROBE_URL, 5)
    cookiefile = Path(ExplodingYoutubeDL.last_instance.params["cookiefile"])
    assert not cookiefile.exists()  # T-COOKIES-6: no orphan tempfile on failure

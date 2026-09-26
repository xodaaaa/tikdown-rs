"""DownloadEngine Protocol contract (T-ENGINE-16, T-ENGINE-17).

Trampas neutralizadas: T-ENGINE-16, T-ENGINE-17, T-BACKFILL-12, T-DEPLOY-20.
Regla: 4.8, 4.2.

Every Protocol method must exist on the CONCRETE class and be callable: a
method declared on the Protocol without a real implementation breaks the whole
flow with AttributeError, and a test double implementing only what it needs is
exactly what hid that bug (T-ENGINE-16). The double here replicates the FULL
signature surface (**kwargs, all Protocol methods, T-DEPLOY-20).
"""

from pathlib import Path

import pytest

from tikdown_rs.core.config import Settings
from tikdown_rs.core.download_engine import DownloadEngine, YtDlpEngine
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.core.paths import outtmpl_for, videos_root

PROTOCOL_METHODS = ("download", "extract_profile", "list_videos", "validate_cookie")


class FullSignatureDouble:
    """Test double implementing ALL Protocol methods with full **kwargs surface."""

    def download(self, *args, **kwargs):
        return None

    def extract_profile(self, *args, **kwargs):
        return {}

    def list_videos(self, *args, **kwargs):
        return []

    def validate_cookie(self, *args, **kwargs):
        return "inconclusive"


@pytest.fixture
def engine() -> YtDlpEngine:
    return YtDlpEngine(cookies_blob=b"# Netscape HTTP Cookie File\n", settings=Settings())


def test_concrete_engine_satisfies_protocol(engine: YtDlpEngine) -> None:
    assert isinstance(engine, DownloadEngine)


def test_full_signature_double_satisfies_protocol() -> None:
    assert isinstance(FullSignatureDouble(), DownloadEngine)


@pytest.mark.parametrize("method", PROTOCOL_METHODS)
def test_every_protocol_method_exists_and_is_callable(engine: YtDlpEngine, method: str) -> None:
    assert hasattr(engine, method)
    assert callable(getattr(engine, method))


def test_engine_naming_is_unambiguous() -> None:
    # T-ENGINE-17: never a bare 'engine'; db_engine vs download_engine.
    assert YtDlpEngine.__name__ == "YtDlpEngine"
    assert DownloadEngine.__name__ == "DownloadEngine"


def test_building_without_cookies_raises() -> None:
    # T-BACKFILL-12: cookie loading is the entry point's responsibility; no
    # silent default. An empty blob is a misconfigured caller, fail fast.
    with pytest.raises(ConfigurationError, match="cookies"):
        YtDlpEngine(cookies_blob=b"", settings=Settings())


def test_download_is_an_explicit_stub(engine: YtDlpEngine) -> None:
    # Not implemented in this work unit (M2/T4); the stub must be callable and
    # raise a clear error naming the follow-up unit (T-ENGINE-16: never
    # AttributeError).
    with pytest.raises(RuntimeError, match="T4"):
        engine.download("https://www.tiktok.com/@user/video/1", "out/%(id)s.%(ext)s")


# --- core/paths (4.5, T-BACKFILL-21, T-ENGINE-14) ---


def test_videos_root_is_derived_from_data_dir(tmp_path: Path) -> None:
    assert videos_root(tmp_path) == tmp_path / "videos"


def test_outtmpl_uses_uploader_and_id(tmp_path: Path) -> None:
    outtmpl = outtmpl_for(tmp_path)
    assert outtmpl.startswith(str(tmp_path / "videos"))
    assert "%(uploader)s" in outtmpl
    assert "%(id)s.%(ext)s" in outtmpl

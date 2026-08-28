"""e04s01 — DownloadEngine: formato §4.2, targets objetos (L-D1), timeout (T23)."""
# story: e04s01

from tikdown_rs.core.download_engine import DEFAULT_FORMAT, RETRY_FORMAT, YtDlpEngine


class _FakeTarget:
    """Simula un ImpersonateTarget (objeto, no string — L-D1)."""

    def __init__(self, name):
        self.name = name


def test_formato_default_contiene_ramas():
    """§4.2: el formato por defecto incluye las ramas DASH y progresiva."""
    assert "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]" in DEFAULT_FORMAT
    assert "best[height<=1080]" in DEFAULT_FORMAT
    assert DEFAULT_FORMAT.split("/")[-1] == "best"


def test_retry_format_prioriza_video():
    """§4.2: el formato de reintento prioriza pista de vídeo explícitamente."""
    assert RETRY_FORMAT.startswith("bestvideo[ext=mp4]+bestaudio[ext=m4a]")


def test_rotacion_targets_objetos_ld1():
    """L-D1: los targets son objetos, y rotan round-robin."""
    t1, t2 = _FakeTarget("a"), _FakeTarget("b")
    engine = YtDlpEngine(impersonate_targets=[t1, t2])
    assert engine._next_target() is t1
    assert engine._next_target() is t2
    assert engine._next_target() is t1  # round-robin


def test_ydl_params_impersonate_objeto():
    """L-D1: params['impersonate'] es el objeto, no un string."""
    t = _FakeTarget("chrome-133")
    engine = YtDlpEngine(impersonate_targets=[t])
    params = engine._ydl_params(t, DEFAULT_FORMAT, "%(id)s.%(ext)s")
    assert params["impersonate"] is t
    assert params["merge_output_format"] == "mp4"

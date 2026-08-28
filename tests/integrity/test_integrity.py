"""e09s01 — integridad: verify (tamaño/SHA/ffprobe), T13 (--), T55 (slideshow)."""
# story: e09s01
from pathlib import Path

from tikdown_rs.services.integrity import ffprobe_args, verify_video


def test_verify_video_ok(tmp_path, monkeypatch):
    """verify_video: tamaño + SHA-256 + ffprobe ok → resultado válido."""
    f = tmp_path / "video.mp4"
    f.write_bytes(b"fake-video-data" * 100)

    class _ProbeResult:
        def __init__(self):
            self.stdout = '{"streams": [{"codec_type": "video", "duration": "5.0"}]}'
            self.returncode = 0

    monkeypatch.setattr("subprocess.run", lambda *a, **k: _ProbeResult())
    result = verify_video(f)
    assert result["ok"] is True
    assert len(result["sha256"]) == 64  # SHA-256 hex
    assert result["size"] > 0


def test_verify_archivo_ausente_falla():
    """Archivo ausente → no ok (nunca 'downloaded' sin verificar, §4.6)."""
    result = verify_video(Path("/no/existe.mp4"))
    assert result["ok"] is False


def test_ffprobe_args_con_doble_guion_t13():
    """T13: ffprobe con lista de argumentos + '--' antes de la ruta."""
    args = ffprobe_args(Path("-archivo-raro.mp4"))
    assert "--" in args
    idx = args.index("--")
    assert args[idx + 1].endswith("-archivo-raro.mp4")  # la ruta va tras --


def test_verify_archivo_vacio_falla(tmp_path, monkeypatch):
    """Tamaño 0 → fallo de integridad."""
    f = tmp_path / "vacio.mp4"
    f.write_bytes(b"")
    result = verify_video(f)
    assert result["ok"] is False


def test_slideshow_skipped_t55():
    """T55: expected_has_video=false → skipped (no fallo)."""
    from tikdown_rs.services.videos import classify_integrity

    assert classify_integrity(expected_has_video=False, has_video_stream=False) == "skipped"
    # expected true sin pista → integrity (fallo real)
    assert classify_integrity(expected_has_video=True, has_video_stream=False) == "integrity"
    # ok
    assert classify_integrity(expected_has_video=True, has_video_stream=True) == "downloaded"

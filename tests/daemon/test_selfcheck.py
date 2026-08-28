"""e02s02 — selfcheck: impersonación (T6), ffmpeg/ffprobe (T46), crypto (T16/T4)."""

# story: e02s02
import pytest

from tikdown_rs.core.verify import (
    selfcheck_ffmpeg,
    selfcheck_impersonation,
)


class _FakeTarget:
    def __init__(self, name):
        self.name = name


def test_impersonation_exito(monkeypatch):
    """Selfcheck de impersonación pasa con targets disponibles."""
    fake_targets = [(_FakeTarget("chrome-133"), "curl_cffi")]
    monkeypatch.setattr(
        "yt_dlp.YoutubeDL._get_available_impersonate_targets",
        lambda self: fake_targets,
    )
    result = selfcheck_impersonation()
    assert result is True


def test_impersonation_curl_cffi_ausente(monkeypatch):
    """T6 causa 1: curl-cffi ausente → SystemExit."""
    monkeypatch.setattr(
        "yt_dlp.YoutubeDL._get_available_impersonate_targets",
        lambda self: [],
    )
    # Simular que curl_cffi no está importable
    import sys

    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    with pytest.raises(SystemExit):
        selfcheck_impersonation()


def test_impersonation_targets_vacios(monkeypatch):
    """T6 causa 3: targets vacíos pese a librería correcta → SystemExit."""
    monkeypatch.setattr(
        "yt_dlp.YoutubeDL._get_available_impersonate_targets",
        lambda self: [],
    )
    with pytest.raises(SystemExit):
        selfcheck_impersonation()


def test_ffmpeg_ffprobe_presentes(monkeypatch):
    """T46: ffmpeg/ffprobe presentes → OK."""
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    result = selfcheck_ffmpeg()
    assert result is True


def test_ffmpeg_ausente(monkeypatch):
    """T46: ffmpeg ausente → falla."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(SystemExit):
        selfcheck_ffmpeg()


def test_ytdlp_version_interna_t4():
    """T4: versión interna de yt-dlp es accesible (coincide con tag GitHub)."""
    from tikdown_rs.core.verify import ytdlp_version_internal

    v = ytdlp_version_internal()
    assert isinstance(v, str) and len(v) > 0


def test_selfcheck_crypto_tabla_ausente(tmp_path):
    """T16: tabla cookies ausente → informativo, NO fallo."""
    from cryptography.fernet import Fernet

    from tikdown_rs.core.verify import selfcheck_crypto

    key = Fernet.generate_key().decode()
    db = tmp_path / "sin_cookies.db"
    db.write_bytes(b"")  # DB vacía/sin esquema
    result = selfcheck_crypto(key, db_path=str(db))
    assert result is True


def test_selfcheck_crypto_clave_invalida():
    """T16: clave Fernet inválida → SystemExit(1)."""
    from tikdown_rs.core.verify import selfcheck_crypto

    with pytest.raises(SystemExit):
        selfcheck_crypto("clave-no-fernet", db_path=None)

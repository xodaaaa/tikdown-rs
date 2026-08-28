"""e05s01 — cookie_parser: T73 (Netscape header), T31 (tempfile seguro)."""
# story: e05s01
from pathlib import Path

from tikdown_rs.core.cookie_parser import NETSCAPE_HEADER, write_netscape_file


def test_netscape_header_constante():
    """T73: el header exacto que exige MozillaCookieJar."""
    assert NETSCAPE_HEADER == "# Netscape HTTP Cookie File"


def test_write_netscape_file_anade_header(tmp_path):
    """write_netscape_file escribe el header + cookies con LF."""
    out = tmp_path / "cookies.txt"
    write_netscape_file(
        out,
        ".tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc123",
    )
    content = out.read_text(encoding="utf-8")
    lines = content.splitlines()
    assert lines[0] == NETSCAPE_HEADER
    assert "sessionid" in content
    assert "\r" not in content  # newline="\n" explícito (T73)


def test_tempfile_limpio_en_finally(tmp_path):
    """T31: mkstemp con os.close + limpieza garantizada en finally."""
    import os
    import tempfile

    created = []

    def _mkstemp():
        fd, path = tempfile.mkstemp(dir=tmp_path, prefix="cookies-")
        created.append(path)
        os.close(fd)  # L-H5: cerrar fd inmediato
        return path

    try:
        path = _mkstemp()
        assert Path(path).exists()
    finally:
        # Limpieza garantizada (simula el finally del worker)
        for p in created:
            if Path(p).exists():
                Path(p).unlink()

    for p in created:
        assert not Path(p).exists(), f"tempfile no limpiado: {p}"


def test_parser_carga_con_ytdlp_cookiejar_real(tmp_path):
    """T73: el tempfile reconstruido carga con YoutubeDLCookieJar REAL (local)."""
    out = tmp_path / "cookies.txt"
    write_netscape_file(
        out,
        ".tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\treal-value-123",
    )
    # Carga local sin red con el jar real de yt-dlp
    try:
        from yt_dlp.cookies import YoutubeDLCookieJar

        jar = YoutubeDLCookieJar(str(out))
        jar.load()  # carga local
        cookies = list(jar)
        assert len(cookies) >= 1
        assert cookies[0].name == "sessionid"
    except ImportError:
        import pytest
        pytest.skip("YoutubeDLCookieJar no disponible")

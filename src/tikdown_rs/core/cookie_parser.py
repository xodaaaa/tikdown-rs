"""Parser de cookies — core/cookie_parser.py (T73).

El MozillaCookieJar de CPython (que usa YoutubeDLCookieJar) exige que la
PRIMERA línea sea '# Netscape HTTP Cookie File'. El blob cifrado guarda solo
las líneas de cookies; todo tempfile reconstruido lleva el header + newline="\n".

story: e05s01
"""

from __future__ import annotations

from pathlib import Path

# T73: el magic header exacto que exige MozillaCookieJar._really_load
NETSCAPE_HEADER = "# Netscape HTTP Cookie File"


def write_netscape_file(path: Path, cookie_lines: str) -> None:
    """Escribe un archivo Netscape con el header + cookies (LF explícito).

    Si el blob ya trae el header, no lo duplica (cubre blobs viejos, T73).
    """
    lines = cookie_lines.strip().splitlines()
    if not lines or lines[0].strip() != NETSCAPE_HEADER:
        lines.insert(0, NETSCAPE_HEADER)
    with open(path, "w", encoding="utf-8", newline="\n") as f:  # newline="\n" (T73)
        f.write("\n".join(lines) + "\n")

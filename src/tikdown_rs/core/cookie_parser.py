"""Cookie format detection and canonical Netscape conversion.

Trampas neutralizadas: T-COOKIES-1, T-COOKIES-5, T-COOKIES-6, T-COOKIES-7.
Regla: 4.3, 7.

T-COOKIES-1: the real parser (YoutubeDLCookieJar -> MozillaCookieJar._really_load)
refuses a Netscape file without the magic header `# Netscape HTTP Cookie File`
as the first line; a tolerant homemade parser would mask that rejection. So the
canonical text ALWAYS starts with the header, exactly once, and is written with
newline="\n" semantics. Tests load the tempfile with the REAL jar.

T-COOKIES-7: on Windows an open fd prevents unlink; os.close(fd) runs
immediately after mkstemp.

T-COOKIES-6: a mid-write failure must not leave an orphan tempfile; cleanup
runs in the failure path of write_canonical_netscape_tempfile.
"""

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger("tikdown_rs.core.cookie_parser")

HEADER = "# Netscape HTTP Cookie File"
_DEFAULT_DOMAIN = ".tiktok.com"
# T-COOKIES-5: absurd expiry epochs overflow datetime.fromtimestamp; clamp
# everything beyond this ceiling before any fromtimestamp use.
_MAX_EXPIRY_TS = int(datetime(2100, 12, 31, tzinfo=UTC).timestamp())


def clamp_expiry(ts: int) -> int:
    """Clamp an absurd expiry epoch to 2100-12-31 before fromtimestamp (T-COOKIES-5).

    This is the clamp for STORING expiry values; never reuse it for
    countdowns -- a countdown must never report a clamped fake future (7).
    """
    return min(ts, _MAX_EXPIRY_TS)


def detect_cookie_format(text: str) -> Literal["netscape", "json", "cookie-string"]:
    """Classify the cookie source text (7: Netscape / JSON list / cookie-string)."""
    stripped = text.strip()
    if stripped.startswith("["):
        return "json"
    lines = text.splitlines()
    first = lines[0].strip() if lines else ""
    if first == HEADER:
        return "netscape"
    if any(line and not line.lstrip().startswith("#") and line.count("\t") == 6 for line in lines):
        return "netscape"
    return "cookie-string"


def _netscape_line(
    domain: str,
    include_subdomains: str,
    path: str,
    secure: bool,
    expires: int,
    name: str,
    value: str,
) -> str:
    return "\t".join(
        [
            domain,
            include_subdomains,
            path or "/",
            "TRUE" if secure else "FALSE",
            str(expires),
            name,
            value,
        ]
    )


def _json_to_lines(text: str) -> list[str]:
    cookies = json.loads(text)
    if not isinstance(cookies, list):
        raise TypeError("JSON cookies must be a list of cookie objects")
    lines: list[str] = []
    for cookie in cookies:
        domain = cookie.get("domain") or _DEFAULT_DOMAIN
        lines.append(
            _netscape_line(
                domain=domain,
                include_subdomains="TRUE" if domain.startswith(".") else "FALSE",
                path=cookie.get("path") or "/",
                secure=bool(cookie.get("secure")),
                expires=clamp_expiry(int(cookie.get("expires") or 0)),  # T-COOKIES-5
                name=cookie["name"],
                value=cookie.get("value", ""),
            )
        )
    return lines


def _cookie_string_to_lines(text: str) -> list[str]:
    # 7: cookie-string defaults: .tiktok.com, /, secure, session cookie (0).
    lines: list[str] = []
    for pair in text.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        lines.append(
            _netscape_line(_DEFAULT_DOMAIN, "TRUE", "/", True, 0, name.strip(), value.strip())
        )
    return lines


def to_canonical_netscape(text: str) -> str:
    """Convert ANY accepted format to canonical Netscape text (4.3, 7).

    The magic header is the FIRST line, never duplicated. Unix newlines
    (newline="\\n" semantics): lines joined with \\n, trailing \\n.
    """
    fmt = detect_cookie_format(text)
    if fmt == "json":
        body = _json_to_lines(text)
    elif fmt == "cookie-string":
        body = _cookie_string_to_lines(text)
    else:
        body = [line for line in text.splitlines() if line.strip() and line.strip() != HEADER]
    return "\n".join([HEADER, *body]) + "\n"


def write_canonical_netscape_tempfile(text: str, dir: Path | None = None) -> Path:
    """Write to_canonical_netscape(text) to a tempfile and return its path.

    T-COOKIES-7: os.close(fd) immediately after mkstemp, so the returned file
    can be unlinked right away (Windows cannot unlink an open fd).
    T-COOKIES-6: a failed write removes the tempfile in the failure path.
    """
    fd, name = tempfile.mkstemp(prefix="tikdown-cookies-", suffix=".txt", dir=dir)
    os.close(fd)  # T-COOKIES-7
    path = Path(name)
    try:
        canonical = to_canonical_netscape(text)
        path.write_text(canonical, encoding="utf-8", newline="\n")
    except BaseException:
        path.unlink(missing_ok=True)  # T-COOKIES-6
        raise
    return path


def warn_missing_sid_tt(text: str) -> list[str]:
    """Warn (never reject) when `sid_tt` is absent (7).

    Returns the missing cookie names; empty list means nothing to warn about.
    """
    names = {
        parts[5]
        for line in to_canonical_netscape(text).splitlines()[1:]
        if len(parts := line.split("\t")) == 7
    }
    if "sid_tt" in names:
        return []
    logger.warning("cookies file has no sid_tt cookie; TikTok may treat the session as logged out")
    return ["sid_tt"]

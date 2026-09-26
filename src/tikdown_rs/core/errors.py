"""Shared exception types for configuration and runtime failures.

Trampas neutralizadas: T-DEPLOY-8 (fail-fast con mensajes accionables),
T-ENGINE-1, T-ENGINE-2, T-ENGINE-3, T-ENGINE-27, T-DATA-3. Reglas: 4.4, 11.1, 5.1.
"""

import re


class ConfigurationError(Exception):
    """A configuration problem the operator must fix before the daemon can run."""


class DownloadTimeoutError(Exception):
    """A download exceeded ``download_timeout_seconds`` (4.5, T-ASYNC-8).

    ``asyncio.wait_for`` does NOT kill the native yt-dlp thread (T-ASYNC-14/15):
    the zombie keeps making requests and may keep writing the outtmpl, which is
    why every retry writes ``.retry-N`` and only renames after integrity. The
    engine counts the zombie in ``YtDlpEngine.zombie_threads``; classifying this
    error is the CALLER's job (4.4 rule 9: a timeout is transient).
    """


AUTH_MARKERS: tuple[str, ...] = (
    "requiring login",
    "login required",
    "log into an account",
    "log in for access",
    "permission to view",
    "account is private",
    "captcha",
    "banned",
    "suspended",
    "session expired",
)


def _markers(*markers: str):
    """Build a rule predicate from literal markers (case-insensitive containment)."""

    def match(haystack: str) -> bool:
        return any(marker in haystack for marker in markers)

    return match


def _unknown_nonzero_status_code(haystack: str) -> bool:
    """Rule 4 tail: an unknown status code other than 0 (4.4)."""
    match = _STATUS_CODE_RE.search(haystack)
    return match is not None and match.group(1) != "0"


_STATUS_CODE_RE = re.compile(r"status code (\d+)")

# The FULL 4.4 table in MANDATORY evaluation order: evaluated top to bottom,
# never as a set. Order facts (4.4 header):
# - Rule 1 (IP/rate-limit block) is evaluated BEFORE the generic 403 of rule 3
#   (T-ENGINE-1: a blocked IP must stay transient, not be misread as a 403).
# - Rule 5 ('status code 0', a degraded anti-bot response, T-ENGINE-3) is
#   evaluated BEFORE rule 4, which would otherwise capture the unknown-status
#   arm and wrongly call degraded content 'definitive'.
# Only rules 2 and 4 are 'definitive'; everything else is transient, 'local'
# (rule 7, disk-full: pauses downloads, never touches breaker/cookies) or
# 'info' (rule 8, empty account: counts for nothing).
_ERROR_RULES: tuple[tuple[str, object, str], ...] = (
    ("rule1-ip-block", _markers("blocked", "ip address is blocked"), "transient"),
    ("rule2-auth", _markers(*AUTH_MARKERS), "definitive"),
    ("rule3-403", _markers("403"), "transient"),
    ("rule5-status-code-0", _markers("status code 0"), "transient"),
    (
        "rule4-nonexistent",
        lambda h: (
            _markers("video unavailable", "404", "removed")(h) or _unknown_nonzero_status_code(h)
        ),
        "definitive",
    ),
    (
        "rule6-extractor",
        _markers(
            "keeps sending the same page",
            "unable to extract",
            "no entries",
            "invalid json",
            "unable to parse json",
            "jsondecodeerror",
            "unable to solve js challenge",
            "unable to extract challenge data",
            "please wait",
        ),
        "transient",
    ),
    ("rule7-local-disk", _markers("no space left", "enospc"), "local"),
    ("rule8-empty-account", _markers("does not have any videos posted"), "info"),
    (
        "rule9-rate-timeout",
        _markers("429", "timed out", "timeout", "service unavailable"),
        "transient",
    ),
)


def classify_error(exc_or_message: str | BaseException) -> str:
    """Project-wide error classifier (4.4, T-DATA-3: ONE classifier, no parallels).

    Matching is case-insensitive over the FULL exception text including the
    chained ``__cause__`` walk, never over short isolated substrings.

    The FULL 4.4 table is evaluated in MANDATORY order (see _ERROR_RULES);
    anything unmatched is rule 10: 'transient' (never definitive by default).
    Return values: 'definitive' | 'transient' | 'local' | 'info'.

    T-ENGINE-2 warning about over-broad markers: 'account' alone would match
    benign messages such as "the account exists and is healthy" and flip real
    content to definitive; 'not available' alone would capture rule 5's
    "video not available, status code 0" (a transient anti-bot response) as
    definitive content-loss. The table uses the exact literals the extractor
    emits ('account is private', 'video unavailable', ...) instead.

    Callers must compare with ``==`` against the category they act on (M1's
    services/cookies.validate_cookie checks ``== 'definitive'``); 'local' and
    'info' are distinct actionable categories, not flavors of transient.
    """
    if isinstance(exc_or_message, BaseException):
        parts: list[str] = []
        seen: set[int] = set()
        current: BaseException | None = exc_or_message
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            parts.append(str(current))
            parts.append(repr(current))
            current = current.__cause__
        haystack = " \n".join(parts).lower()
    else:
        haystack = exc_or_message.lower()
    for rule_name, matches, category in _ERROR_RULES:
        if matches(haystack):
            return category
    return "transient"  # rule 10: never definitive by default

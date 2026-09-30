"""Full 4.4 error classification table: every literal, in-order precedence.

Trampas neutralizadas: T-ENGINE-1, T-ENGINE-2, T-ENGINE-3, T-ENGINE-4,
T-ENGINE-27, T-ENGINE-30, T-DATA-3. Regla: 4.4.

The table is evaluated top to bottom over the FULL exception string including
chained causes, case-insensitive (4.4 header). The mandatory ORDER is exercised
here: rule 1 before generic 403 (T-ENGINE-1), rule 5 before rule 4 (T-ENGINE-3).
"""

import json

import pytest

from tikdown_rs.core.errors import classify_error


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # Rule 1: IP/rate-limit blocking markers -> transient, BEFORE generic 403.
        ("IP address is blocked", "transient"),
        ("you are blocked by the server", "transient"),
        # Rule 1 beats rule 3: T-ENGINE-1 (order matters).
        ("HTTP Error 403: ip address is blocked", "transient"),
        # Rule 2: auth markers -> definitive.
        ("ERROR: requiring login", "definitive"),
        ("login required to view", "definitive"),
        ("please log into an account", "definitive"),
        ("log in for access to this content", "definitive"),
        ("you do not have permission to view", "definitive"),
        ("this account is private", "definitive"),
        ("solve the captcha to continue", "definitive"),
        ("this user is banned", "definitive"),
        ("the account is suspended", "definitive"),
        ("your session expired", "definitive"),
        # Rule 3: 403 without auth evidence -> transient (T-ENGINE-1).
        ("HTTP Error 403: Forbidden", "transient"),
        # Rule 4: nonexistent content -> definitive.
        ("video unavailable", "definitive"),
        ("HTTP Error 404: Not Found", "definitive"),
        ("this video has been removed", "definitive"),
        ("video not available, status code 7", "definitive"),
        # T-ENGINE-34 (audit 2.4): a 19-digit TikTok video ID containing "404"
        # inside a transient message must NOT flip the category to definitive;
        # digit runs >= 15 are redacted before rule matching.
        ("ERROR: [tiktok] 7345404041234567890: Unable to download webpage", "transient"),
        ("download of 7345404041234567890 exceeded download_timeout_seconds=600", "transient"),
        # Rule 5 beats rule 4 (T-ENGINE-3): degraded anti-bot response, NOT missing content.
        ("video not available, status code 0", "transient"),
        ("Video Not Available, Status Code 0", "transient"),
        # Rule 6: extractor degradation / unsolved challenge -> transient.
        ("TikTok keeps sending the same page", "transient"),
        ("Unable to extract data", "transient"),
        ("the feed returned no entries", "transient"),
        ("invalid json in response", "transient"),
        ("unable to parse json: Expecting value", "transient"),
        ("Unable to solve JS challenge", "transient"),
        ("Unable to extract challenge data (T-ENGINE-30 WAF)", "transient"),
        ("Please wait... challenge in progress", "transient"),
        # Rule 7: local disk failure -> 'local' (never breaker/cookies, T-ENGINE-27).
        ("no space left on device", "local"),
        ("OSError(28, 'No space left on device')", "local"),
        # Rule 8: empty account is informational, counts for nothing.
        ("user does not have any videos posted", "info"),
        # Rule 9: rate limit / timeout / service unavailable -> transient.
        ("HTTP Error 429: Too Many Requests", "transient"),
        ("connection timed out", "transient"),
        ("socket.timeout: The read operation timed out", "transient"),
        ("HTTP Error 503: service unavailable", "transient"),
        # Rule 10: anything else -> transient (never definitive by default).
        ("something entirely unexpected happened", "transient"),
        # T-ENGINE-2: over-broad markers are NOT markers. Bare 'account' and
        # 'not available' must not classify legitimate content as definitive.
        ("the account exists and is healthy", "transient"),
        ("feature not available in this region", "transient"),
    ],
)
def test_full_table(message: str, expected: str) -> None:
    assert classify_error(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "IP Address Is Blocked",
        "This ACCOUNT IS PRIVATE",
        "Unable To Solve JS Challenge",
        "Keeps Sending The Same Page",
        "No Space Left On Device",
    ],
)
def test_case_insensitive(message: str) -> None:
    assert classify_error(message) in {"transient", "definitive", "local", "info"}


def test_case_insensitive_exact_categories() -> None:
    assert classify_error("IP ADDRESS IS BLOCKED") == "transient"
    assert classify_error("NO SPACE LEFT ON DEVICE") == "local"
    assert classify_error("DOES NOT HAVE ANY VIDEOS POSTED") == "info"


def test_chained_cause_is_walked() -> None:
    try:
        try:
            raise ValueError("this content is requiring login")
        except ValueError as cause:
            raise RuntimeError("HTTP Error 403") from cause
    except RuntimeError as exc:
        assert classify_error(exc) == "definitive"


def test_chained_cause_invalid_json_is_transient() -> None:
    try:
        try:
            raise json.JSONDecodeError("Expecting value", "<doc>", 0)
        except json.JSONDecodeError as cause:
            raise RuntimeError("DownloadError: extraction failed") from cause
    except RuntimeError as exc:
        assert classify_error(exc) == "transient"


def test_auth_in_cause_beats_generic_403_in_outer_message() -> None:
    # The full string INCLUDING the cause is classified: auth evidence wins.
    try:
        try:
            raise ValueError("log in for access")
        except ValueError as cause:
            raise RuntimeError("HTTP Error 403") from cause
    except RuntimeError as exc:
        assert classify_error(exc) == "definitive"


def test_categories_are_the_documented_set() -> None:
    for message in ("blocked", "requiring login", "no space left", "no videos posted", "xyz"):
        assert classify_error(message) in {"definitive", "transient", "local", "info"}

"""Cookie validation: three-state probe, get_working_cookie, error classification.

Trampas neutralizadas: T-COOKIES-2, T-COOKIES-3, T-COOKIES-4, T-COOKIES-5,
T-DB-15, T-DATA-3. Regla: 4.4 (rule 2), 7.

All probes here are deterministic fakes: no network, no yt_dlp.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tikdown_rs.cli.main import app
from tikdown_rs.core.cookie_parser import clamp_expiry
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.errors import ConfigurationError, classify_error
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.core.verify import entries_have_video
from tikdown_rs.models import Cookie
from tikdown_rs.services.cookies import get_working_cookie, validate_cookie

runner = CliRunner()

VIDEO_ENTRY = {
    "url": "https://www.tiktok.com/@probe/video/1",
    "duration": 30.5,
}
SLIDESHOW_ENTRY = {
    "url": "https://www.tiktok.com/@probe/photo/1",
    "duration": 0,
}
AUDIO_ONLY_SLIDESHOW_ENTRY = {
    "url": "https://www.tiktok.com/@probe/photo/2",
    "duration": 0,
}


@pytest.fixture
async def migrated_factory(tmp_path: Path):
    """Engine + session factory over a REAL file DB migrated with run_migrations."""
    import asyncio

    await asyncio.to_thread(run_migrations, tmp_path)
    engine = create_db_engine(sqlite_url_for(tmp_path))
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


async def _insert_cookie(factory, state: str = "inconclusive", validated_at: str | None = None):
    now = datetime.now(UTC).isoformat()
    async with factory() as session:
        row = Cookie(
            label="t",
            cookie_blob=b"# Netscape HTTP Cookie File\n",
            validation_state=state,
            last_validated_at=validated_at,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _get_row(factory, cookie_id: int) -> Cookie:
    async with factory() as session:
        row = await session.get(Cookie, cookie_id)
        await session.refresh(row)
        return row


# --- classify_error (4.4 rule 2, T-DATA-3) ---


@pytest.mark.parametrize(
    "marker",
    [
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
    ],
)
def test_auth_marker_is_definitive(marker: str) -> None:
    assert classify_error(f"ERROR: [{marker}] something else happened") == "definitive"


@pytest.mark.parametrize(
    "message",
    [
        "HTTP Error 403: Forbidden",
        "429 Too Many Requests",
        "connection timed out",
        "Unable to extract challenge data",
        "video unavailable; status code 0",
    ],
)
def test_non_auth_error_is_transient(message: str) -> None:
    assert classify_error(message) == "transient"


def test_no_videos_posted_is_info_not_transient() -> None:
    """§4.4 rule 8: informative literal, counts for nothing (not transient)."""
    assert classify_error("user does not have any videos posted") == "info"


def test_classify_error_is_case_insensitive() -> None:
    assert classify_error("Requiring Login To Steam") == "definitive"
    assert classify_error("This ACCOUNT IS PRIVATE") == "definitive"


def test_classify_error_walks_chained_cause() -> None:
    try:
        try:
            raise ValueError("this content is requiring login")
        except ValueError as cause:
            raise RuntimeError("HTTP Error 403") from cause
    except RuntimeError as exc:
        assert classify_error(exc) == "definitive"


# --- entries_have_video (T-COOKIES-2) ---


def test_entries_have_video_by_duration() -> None:
    assert entries_have_video([dict(SLIDESHOW_ENTRY), dict(VIDEO_ENTRY)]) is True


def test_entries_have_video_by_url() -> None:
    entry = {"url": "https://www.tiktok.com/@u/video/123", "duration": None}
    assert entries_have_video([entry]) is True


def test_slideshow_only_entries_have_no_video() -> None:
    assert entries_have_video([dict(SLIDESHOW_ENTRY), dict(AUDIO_ONLY_SLIDESHOW_ENTRY)]) is False


def test_no_entries_has_no_video() -> None:
    assert entries_have_video([]) is False


# --- validate_cookie (7: three-state validation, T-COOKIES-3, T-DB-15) ---


async def test_valid_probe_persists_valid_and_timestamp(migrated_factory) -> None:
    cookie_id = await _insert_cookie(migrated_factory)
    calls: list[str] = []

    def probe_fn(blob, url, max_entries):
        calls.append(url)
        assert isinstance(blob, bytes)
        return True

    verdict = await validate_cookie(migrated_factory, cookie_id, ["u1", "u2"], probe_fn, 5)
    assert verdict == "valid"
    assert calls == ["u1"]  # first URL suffices, no fallback
    row = await _get_row(migrated_factory, cookie_id)
    assert row.validation_state == "valid"
    assert row.last_validated_at is not None
    assert row.last_validation_reason


async def test_confirmed_auth_failure_persists_invalid(migrated_factory) -> None:
    cookie_id = await _insert_cookie(migrated_factory)

    def probe_fn(blob, url, max_entries):
        raise RuntimeError("ERROR: this content is requiring login")

    verdict = await validate_cookie(migrated_factory, cookie_id, ["u1"], probe_fn, 5)
    assert verdict == "invalid"
    row = await _get_row(migrated_factory, cookie_id)
    assert row.validation_state == "invalid"
    assert row.last_validated_at is not None
    assert row.last_validation_reason


async def test_transient_failure_on_all_urls_is_global_inconclusive(
    migrated_factory,
) -> None:
    cookie_id = await _insert_cookie(migrated_factory, state="inconclusive")
    calls: list[str] = []

    def probe_fn(blob, url, max_entries):
        calls.append(url)
        raise RuntimeError("HTTP Error 403: Forbidden")

    verdict = await validate_cookie(migrated_factory, cookie_id, ["u1", "u2"], probe_fn, 5)
    assert verdict == "inconclusive"
    assert calls == ["u1", "u2"]  # every candidate tried
    row = await _get_row(migrated_factory, cookie_id)
    # T-COOKIES-3: state and timestamp untouched by a broken/degraded probe.
    assert row.validation_state == "inconclusive"
    assert row.last_validated_at is None


async def test_slideshow_first_url_falls_back_to_second(migrated_factory) -> None:
    cookie_id = await _insert_cookie(migrated_factory)

    def probe_fn(blob, url, max_entries):
        # T-COOKIES-2: slideshow-only feed on the first candidate.
        return url != "u1"

    verdict = await validate_cookie(migrated_factory, cookie_id, ["u1", "u2"], probe_fn, 5)
    assert verdict == "valid"


async def test_transient_first_url_falls_back_to_second(migrated_factory) -> None:
    cookie_id = await _insert_cookie(migrated_factory)

    def probe_fn(blob, url, max_entries):
        if url == "u1":
            raise RuntimeError("socket timeout")
        return True

    verdict = await validate_cookie(migrated_factory, cookie_id, ["u1", "u2"], probe_fn, 5)
    assert verdict == "valid"


async def test_entries_without_video_on_all_urls_is_inconclusive(migrated_factory) -> None:
    cookie_id = await _insert_cookie(
        migrated_factory, state="valid", validated_at="2026-01-01T00:00:00+00:00"
    )

    def probe_fn(blob, url, max_entries):
        return False

    verdict = await validate_cookie(migrated_factory, cookie_id, ["u1", "u2"], probe_fn, 5)
    assert verdict == "inconclusive"
    row = await _get_row(migrated_factory, cookie_id)
    assert row.validation_state == "valid"  # untouched
    assert row.last_validated_at == "2026-01-01T00:00:00+00:00"


async def test_broken_probe_never_invalidates_valid_cookie(migrated_factory) -> None:
    cookie_id = await _insert_cookie(
        migrated_factory, state="valid", validated_at="2026-01-01T00:00:00+00:00"
    )

    def probe_fn(blob, url, max_entries):
        raise RuntimeError("connection reset by peer")

    verdict = await validate_cookie(migrated_factory, cookie_id, ["u1", "u2"], probe_fn, 5)
    assert verdict == "inconclusive"
    row = await _get_row(migrated_factory, cookie_id)
    assert row.validation_state == "valid"
    assert row.last_validated_at == "2026-01-01T00:00:00+00:00"


async def test_unknown_cookie_id_raises(migrated_factory) -> None:
    def probe_fn(blob, url, max_entries):
        raise AssertionError("probe must not run for an unknown cookie")

    with pytest.raises(ConfigurationError, match="unknown cookie id"):
        await validate_cookie(migrated_factory, 999, ["u1"], probe_fn, 5)


# --- get_working_cookie (7, T-COOKIES-4) ---


async def test_valid_beats_inconclusive_regardless_of_order(migrated_factory) -> None:
    valid_id = await _insert_cookie(
        migrated_factory, state="valid", validated_at="2026-01-01T00:00:00+00:00"
    )
    await _insert_cookie(
        migrated_factory, state="inconclusive", validated_at="2026-02-01T00:00:00+00:00"
    )
    row = await get_working_cookie(migrated_factory)
    assert row is not None
    assert row.id == valid_id


async def test_inconclusive_is_returned_not_rejected(
    migrated_factory, caplog: pytest.LogCaptureFixture
) -> None:
    await _insert_cookie(
        migrated_factory, state="inconclusive", validated_at="2026-01-01T00:00:00+00:00"
    )
    with caplog.at_level(logging.INFO, logger="tikdown_rs.services.cookies"):
        row = await get_working_cookie(migrated_factory)
    assert row is not None
    assert row.validation_state == "inconclusive"
    assert "inconclusive" in caplog.text


async def test_never_checked_cookie_is_still_a_candidate(migrated_factory) -> None:
    await _insert_cookie(migrated_factory, state="inconclusive", validated_at=None)
    row = await get_working_cookie(migrated_factory)
    assert row is not None
    assert row.last_validated_at is None


async def test_only_invalid_yields_none(migrated_factory) -> None:
    await _insert_cookie(
        migrated_factory, state="invalid", validated_at="2026-01-01T00:00:00+00:00"
    )
    assert await get_working_cookie(migrated_factory) is None


async def test_empty_store_yields_none(migrated_factory) -> None:
    assert await get_working_cookie(migrated_factory) is None


# --- expiry clamp + countdown (T-COOKIES-5) ---


def test_clamp_expiry_caps_absurd_epoch() -> None:
    year_2100 = int(datetime(2100, 12, 31, tzinfo=UTC).timestamp())
    assert clamp_expiry(999_999_999_999) == year_2100


def test_clamp_expiry_keeps_normal_value() -> None:
    assert clamp_expiry(1_700_000_000) == 1_700_000_000


def seconds_until_expiry(expiry_ts: int, now_ts: int) -> int:
    """Countdown helper (moved from core/cookie_parser.py, test-only there):
    positive seconds if future, 0 if past. Deliberately NOT clamp_expiry-based:
    a countdown must never report a clamped fake future (7)."""
    return max(0, expiry_ts - now_ts)


def test_seconds_until_expiry_future_is_positive() -> None:
    assert seconds_until_expiry(2_000_000_000, 1_700_000_000) == 300_000_000


def test_seconds_until_expiry_past_is_zero() -> None:
    assert seconds_until_expiry(1_000_000_000, 1_700_000_000) == 0


def test_clamp_applied_in_json_canonicalization() -> None:
    from tikdown_rs.core.cookie_parser import to_canonical_netscape

    blob = json.dumps(
        [{"name": "sid_tt", "value": "x", "expires": 999_999_999_999, "domain": ".tiktok.com"}]
    )
    expiry_field = to_canonical_netscape(blob).splitlines()[1].split("\t")[4]
    assert int(expiry_field) == clamp_expiry(999_999_999_999)


# --- CLI `cookies test` (DR-5: no default probe profiles) ---


def test_cli_test_without_probe_urls_fails_with_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "COOKIE_VALIDATION_URL",
        "COOKIE_PROBE_MAX_ENTRIES",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    result = runner.invoke(app, ["cookies", "test", "1"])
    assert result.exit_code == 1
    assert "ERROR" in result.output
    assert "COOKIE_VALIDATION_URL" in result.output
    assert "probe" in result.output.lower()


# --- B1 (T-ASYNC-8): the blocking probe must run OFF the loop thread ---


async def test_cookie_probe_runs_off_the_loop_thread(migrated_factory) -> None:
    """T-ASYNC-8/§1.1.3 (B1/JD-A-001): validate_cookie must offload the
    blocking probe composition (yt-dlp extraction) to a worker thread;
    on-loop, the off-loop assertion raises and the verdict degrades to
    inconclusive instead of valid."""
    import asyncio as _asyncio

    cookie_id = await _insert_cookie(migrated_factory)

    def probe_fn(blob, url, max_entries):
        try:
            _asyncio.get_running_loop()
        except RuntimeError:
            return True
        raise AssertionError("blocking probe ran on the event loop thread")

    verdict = await validate_cookie(migrated_factory, cookie_id, ["u1"], probe_fn, 5)
    assert verdict == "valid"

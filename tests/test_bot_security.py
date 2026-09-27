"""Security core tests for the Telegram bot (plan section 6.3).

Trampas neutralizadas: T-BOT-6 (clip suffix inside 4096), T-BOT-9 (lstrip('@')),
T-BOT-11/T-BOT-18 (guard tolerates missing effective_chat), plus burst and
throttle rules from section 6.3.
"""

from collections.abc import Iterator

import pytest

from tikdown_rs.bot.security import (
    CLIP_SUFFIX,
    COMMAND_THROTTLE_SECONDS,
    MESSAGE_MAX_LENGTH,
    UNAUTHORIZED_BURST_LIMIT,
    UNAUTHORIZED_BURST_WINDOW_SECONDS,
    CallerContext,
    SecurityDecision,
    SecurityGuard,
    clip,
    display_username,
    escape_html,
)


class FakeClock:
    """Deterministic injectable clock (float seconds)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> Iterator[FakeClock]:
    yield FakeClock()


# --- Authorization ---------------------------------------------------------


def test_chat_allowed_user_not_in_list_denied() -> None:
    guard = SecurityGuard(chat_id=100, allowed_user_ids=[1])
    result = guard.check(CallerContext(chat_id=100, user_id=2, has_chat=True))
    assert result.decision is SecurityDecision.DENIED


def test_chat_allowed_user_in_list_allowed() -> None:
    guard = SecurityGuard(chat_id=100, allowed_user_ids=[1])
    result = guard.check(CallerContext(chat_id=100, user_id=1, has_chat=True))
    assert result.decision is SecurityDecision.ALLOWED


def test_chat_allowed_no_user_list_any_user_allowed(clock: FakeClock) -> None:
    guard = SecurityGuard(chat_id=100, allowed_user_ids=None, clock=clock)
    assert guard.check(CallerContext(chat_id=100, user_id=1, has_chat=True)).decision is (
        SecurityDecision.ALLOWED
    )
    clock.advance(COMMAND_THROTTLE_SECONDS + 0.5)
    assert guard.check(CallerContext(chat_id=100, user_id=999, has_chat=True)).decision is (
        SecurityDecision.ALLOWED
    )


def test_empty_user_list_behaves_like_unset() -> None:
    guard = SecurityGuard(chat_id=100, allowed_user_ids=[])
    assert guard.check(CallerContext(chat_id=100, user_id=7, has_chat=True)).decision is (
        SecurityDecision.ALLOWED
    )


def test_wrong_chat_denied_regardless_of_user() -> None:
    guard = SecurityGuard(chat_id=100, allowed_user_ids=[1])
    assert guard.check(CallerContext(chat_id=200, user_id=1, has_chat=True)).decision is (
        SecurityDecision.DENIED
    )
    assert guard.check(CallerContext(chat_id=200, user_id=2, has_chat=True)).decision is (
        SecurityDecision.DENIED
    )


@pytest.mark.parametrize("user_id", [1, None])
def test_missing_chat_denied_without_exception(user_id: int | None) -> None:
    # T-BOT-11/T-BOT-18: updates without effective_chat must be rejected, never raise.
    guard = SecurityGuard(chat_id=100, allowed_user_ids=[1])
    result = guard.check(CallerContext(chat_id=None, user_id=user_id, has_chat=False))
    assert result.decision is SecurityDecision.DENIED


# --- Unauthorized burst ----------------------------------------------------


def test_burst_below_limit_is_plain_denial(clock: FakeClock) -> None:
    guard = SecurityGuard(chat_id=100, allowed_user_ids=[1], clock=clock)
    for _ in range(UNAUTHORIZED_BURST_LIMIT - 1):
        result = guard.check(CallerContext(chat_id=100, user_id=2, has_chat=True))
        assert result.decision is SecurityDecision.DENIED
        assert result.burst_count is None


def test_burst_at_limit_reports_count(clock: FakeClock) -> None:
    guard = SecurityGuard(chat_id=100, allowed_user_ids=[1], clock=clock)
    for _ in range(UNAUTHORIZED_BURST_LIMIT - 1):
        guard.check(CallerContext(chat_id=100, user_id=2, has_chat=True))
    result = guard.check(CallerContext(chat_id=100, user_id=2, has_chat=True))
    assert result.decision is SecurityDecision.BURST
    assert result.burst_count == UNAUTHORIZED_BURST_LIMIT


def test_burst_counter_resets_after_window(clock: FakeClock) -> None:
    guard = SecurityGuard(chat_id=100, allowed_user_ids=[1], clock=clock)
    for _ in range(UNAUTHORIZED_BURST_LIMIT):
        guard.check(CallerContext(chat_id=100, user_id=2, has_chat=True))
    clock.advance(UNAUTHORIZED_BURST_WINDOW_SECONDS + 1)
    result = guard.check(CallerContext(chat_id=100, user_id=2, has_chat=True))
    assert result.decision is SecurityDecision.DENIED
    assert result.burst_count is None


def test_burst_counters_are_per_user(clock: FakeClock) -> None:
    guard = SecurityGuard(chat_id=100, allowed_user_ids=[1], clock=clock)
    for _ in range(UNAUTHORIZED_BURST_LIMIT):
        guard.check(CallerContext(chat_id=100, user_id=2, has_chat=True))
    result = guard.check(CallerContext(chat_id=100, user_id=3, has_chat=True))
    assert result.decision is SecurityDecision.DENIED
    assert result.burst_count is None


# --- Throttle --------------------------------------------------------------


def test_throttle_first_call_passes(clock: FakeClock) -> None:
    guard = SecurityGuard(chat_id=100, allowed_user_ids=None, clock=clock)
    assert guard.check(CallerContext(chat_id=100, user_id=1, has_chat=True)).decision is (
        SecurityDecision.ALLOWED
    )


def test_throttle_second_call_within_window(clock: FakeClock) -> None:
    guard = SecurityGuard(chat_id=100, allowed_user_ids=None, clock=clock)
    guard.check(CallerContext(chat_id=100, user_id=1, has_chat=True))
    clock.advance(COMMAND_THROTTLE_SECONDS - 0.5)
    assert guard.check(CallerContext(chat_id=100, user_id=1, has_chat=True)).decision is (
        SecurityDecision.THROTTLED
    )


def test_throttle_per_chat(clock: FakeClock) -> None:
    guard = SecurityGuard(chat_id=100, allowed_user_ids=None, clock=clock)
    # Authz pins one chat per guard, so per-chat keying is exercised via throttle().
    assert guard.throttle(100) is False
    clock.advance(0.5)
    assert guard.throttle(200) is False  # different chat unaffected
    assert guard.throttle(100) is True


def test_throttle_after_window_passes(clock: FakeClock) -> None:
    guard = SecurityGuard(chat_id=100, allowed_user_ids=None, clock=clock)
    guard.check(CallerContext(chat_id=100, user_id=1, has_chat=True))
    clock.advance(COMMAND_THROTTLE_SECONDS + 0.5)
    assert guard.check(CallerContext(chat_id=100, user_id=1, has_chat=True)).decision is (
        SecurityDecision.ALLOWED
    )


def test_callback_shares_throttle_state_with_commands(clock: FakeClock) -> None:
    guard = SecurityGuard(chat_id=100, allowed_user_ids=None, clock=clock)
    # No source distinction: callbacks and commands go through the same check().
    assert guard.check(CallerContext(chat_id=100, user_id=1, has_chat=True)).decision is (
        SecurityDecision.ALLOWED
    )
    clock.advance(0.5)
    assert guard.check(CallerContext(chat_id=100, user_id=1, has_chat=True)).decision is (
        SecurityDecision.THROTTLED
    )


# --- clip ------------------------------------------------------------------


def test_clip_under_limit_unchanged() -> None:
    assert clip("hello", limit=MESSAGE_MAX_LENGTH) == "hello"


def test_clip_over_limit_fits_and_ends_with_suffix() -> None:
    text = "x" * (MESSAGE_MAX_LENGTH + 100)
    result = clip(text)
    assert len(result) <= MESSAGE_MAX_LENGTH
    assert len(result) == MESSAGE_MAX_LENGTH
    assert result.endswith(CLIP_SUFFIX)


def test_clip_exactly_at_limit_unchanged() -> None:
    text = "y" * MESSAGE_MAX_LENGTH
    assert clip(text) == text


# --- escape_html / display_username ----------------------------------------


def test_escape_html_escapes_dynamic_content() -> None:
    assert escape_html('<b>&"') == "&lt;b&gt;&amp;&quot;"


def test_display_username_strips_leading_at() -> None:
    assert display_username("@user") == "user"
    assert display_username("user") == "user"
    assert display_username("@@user") == "user"


def test_display_username_none_safe() -> None:
    assert display_username(None) == ""

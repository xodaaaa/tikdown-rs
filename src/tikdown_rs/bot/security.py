"""Security core for the Telegram bot: authorization, burst, throttle, rendering helpers.

Trampas neutralizadas (Apéndice A, plan section 6.3):

- T-BOT-5: compact callback encoding budget; the guard is also the shared throttle
  for callback queries (same in-memory state as commands).
- T-BOT-6: ``clip()`` keeps its suffix INSIDE the 4096 limit.
- T-BOT-7: ``escape_html()`` wraps ``html.escape()`` for all dynamic content.
  MarkdownV2 is prohibited and the "can't parse entities" degradation to plain
  text happen at the send site (wiring tasks), not in this module.
- T-BOT-8: burst/throttle here complement ``AIORateLimiter(max_retries=3)``,
  which is constructed at wiring time.
- T-BOT-9: ``display_username()`` uses ``lstrip('@')`` so templates that already
  carry '@' never render it doubled.
- T-BOT-11 / T-BOT-18: the guard tolerates updates without ``effective_chat``
  via the explicit ``has_chat`` flag on ``CallerContext``; such updates are
  rejected as unauthorized, never raising ``AttributeError``.

Burst/throttle rules (section 6.3): >= 5 unauthorized attempts within a 5-minute
window from the same ``from_user.id`` is a burst (distinct result mapped by the
caller to ``bot.unauthorized_attempts_burst`` with the count); at most 1 command
per 2 seconds per ``chat_id``, shared with callback queries.

Limitations (same as the account breaker): all state (burst counters, throttle
timestamps) lives in-process memory on a single ``SecurityGuard`` instance
constructed once per bot; a daemon restart clears it.
"""

from __future__ import annotations

import html
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

UNAUTHORIZED_BURST_LIMIT = 5
UNAUTHORIZED_BURST_WINDOW_SECONDS = 300
COMMAND_THROTTLE_SECONDS = 2
MESSAGE_MAX_LENGTH = 4096
CLIP_SUFFIX = " …[truncated]"


@dataclass(frozen=True)
class CallerContext:
    """PTB-free view of an update's caller; daemon wiring builds it from a real update."""

    chat_id: int | None
    user_id: int | None
    has_chat: bool


class SecurityDecision(Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    BURST = "burst"
    THROTTLED = "throttled"


@dataclass(frozen=True)
class SecurityResult:
    decision: SecurityDecision
    burst_count: int | None = None


def clip(text: str, limit: int = MESSAGE_MAX_LENGTH) -> str:
    """Trim ``text`` to ``limit`` characters, suffix included in the budget (T-BOT-6)."""
    if len(text) <= limit:
        return text
    return text[: limit - len(CLIP_SUFFIX)] + CLIP_SUFFIX


def escape_html(text: str) -> str:
    """Escape dynamic content for ``parse_mode=HTML`` sends (T-BOT-7)."""
    return html.escape(text)


def display_username(name: str | None) -> str:
    """Strip leading '@' so templates that already carry '@' never double it (T-BOT-9)."""
    if name is None:
        return ""
    return name.lstrip("@")


class SecurityGuard:
    """Double-layer authorization + burst tracking + command throttle, all in-memory."""

    def __init__(
        self,
        chat_id: int,
        allowed_user_ids: Sequence[int] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._chat_id = chat_id
        # Unset/empty allowlist => any user inside the authorized chat passes.
        self._allowed_user_ids = tuple(allowed_user_ids) if allowed_user_ids else None
        self._clock = clock
        self._unauthorized_attempts: dict[int | None, list[float]] = {}
        self._last_accepted: dict[int, float] = {}

    def check(self, caller: CallerContext) -> SecurityResult:
        """Authorize, then throttle. One entry point for commands and callbacks."""
        if not self._authorize(caller):
            return self._record_unauthorized(caller.user_id)
        if self.throttle(caller.chat_id):
            return SecurityResult(SecurityDecision.THROTTLED)
        return SecurityResult(SecurityDecision.ALLOWED)

    def _authorize(self, caller: CallerContext) -> bool:
        # Explicit None checks before comparing ids (T-BOT-11/T-BOT-18).
        if not caller.has_chat or caller.chat_id is None:
            return False
        if caller.chat_id != self._chat_id:
            return False
        if self._allowed_user_ids is None:
            return True
        return caller.user_id in self._allowed_user_ids

    def _record_unauthorized(self, user_id: int | None) -> SecurityResult:
        now = self._clock()
        window = self._unauthorized_attempts.setdefault(user_id, [])
        window[:] = [t for t in window if now - t < UNAUTHORIZED_BURST_WINDOW_SECONDS]
        window.append(now)
        if len(window) >= UNAUTHORIZED_BURST_LIMIT:
            return SecurityResult(SecurityDecision.BURST, burst_count=len(window))
        return SecurityResult(SecurityDecision.DENIED)

    def throttle(self, chat_id: int | None) -> bool:
        """Return True if ``chat_id`` is within the 2 s window; else record and pass."""
        now = self._clock()
        last = self._last_accepted.get(chat_id)
        if last is not None and now - last < COMMAND_THROTTLE_SECONDS:
            return True
        self._last_accepted[chat_id] = now
        return False

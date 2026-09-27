"""Bot package: Telegram bot (plan section 6)."""

from tikdown_rs.bot.dispatcher import TikDownBot
from tikdown_rs.bot.pagination import (
    CALLBACK_TTL_SECONDS,
    LIST_CALLBACK_PREFIX,
    LIST_PAGE_SIZE,
    build_list_keyboard,
    parse_list_page_callback,
    render_list_page,
)
from tikdown_rs.bot.security import (
    CallerContext,
    SecurityDecision,
    SecurityGuard,
    SecurityResult,
    clip,
    display_username,
    escape_html,
)
from tikdown_rs.bot.supervision import (
    PollingSupervisor,
    start_bot_polling,
    stop_bot_polling,
)

__all__ = [
    "CALLBACK_TTL_SECONDS",
    "LIST_CALLBACK_PREFIX",
    "LIST_PAGE_SIZE",
    "CallerContext",
    "PollingSupervisor",
    "SecurityDecision",
    "SecurityGuard",
    "SecurityResult",
    "TikDownBot",
    "build_list_keyboard",
    "clip",
    "display_username",
    "escape_html",
    "parse_list_page_callback",
    "render_list_page",
    "start_bot_polling",
    "stop_bot_polling",
]

"""Pure pagination for the Telegram bot ``/list`` command (plan section 6.4).

Trampas neutralizadas (Apéndice A):

- T-BOT-5: ``callback_data`` uses the compact ``"listp:{ts}:{page}"`` encoding,
  asserted to stay within the raw 64-byte API limit.
- T-BOT-7: every dynamic value rendered by ``render_list_page`` goes through
  ``escape_html()`` from ``security.py`` (``parse_mode=HTML`` sends).
- Section 6.4 pagination semantics: page clamping out of range,
  ``total_pages = ceil(n/page_size)``, empty list renders ``"No hay cuentas"``.

The callback TTL (60 s) is enforced here with an injectable clock so the T5
wiring validates expiration *before* re-rendering; ``build_list_keyboard``
returns a plain serializable structure (rows of button dicts) that the T5
wiring converts to an ``InlineKeyboardMarkup`` — no PTB imports here.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from typing import Any

from tikdown_rs.bot.security import escape_html

LIST_PAGE_SIZE = 5
CALLBACK_TTL_SECONDS = 60
LIST_CALLBACK_PREFIX = "listp:"

NO_ACCOUNTS_TEXT = "No hay cuentas"


def render_list_page(
    accounts: Sequence[Any],
    page: int = 0,
    page_size: int = LIST_PAGE_SIZE,
) -> tuple[str, int, int]:
    """Render one page of ``accounts`` as HTML text with its clamped page.

    Accounts are lightweight objects mirroring ``MonitoredAccount`` fields
    (``username``, ``backfill_status``, ``video_count``). Returns
    ``(text, page, total_pages)`` with ``total_pages = ceil(n/page_size)``;
    out-of-range pages clamp to a valid one.
    """
    total = len(accounts)
    total_pages = math.ceil(total / page_size) if total else 0
    if total == 0:
        return NO_ACCOUNTS_TEXT, 0, 0
    page = max(0, min(page, total_pages - 1))
    chunk = accounts[page * page_size : (page + 1) * page_size]
    lines = [f"Cuentas ({page + 1}/{total_pages}):", ""]
    for account in chunk:
        count = account.video_count
        count_text = str(count) if count is not None else "?"
        lines.append(
            f"• @{escape_html(account.username)}"
            f" — {escape_html(account.backfill_status)}"
            f" — {count_text} videos"
        )
    return "\n".join(lines), page, total_pages


def build_list_keyboard(
    page: int,
    total_pages: int,
    ts: int | None = None,
) -> list[list[dict[str, str]]]:
    """Rows of nav buttons for ``page``/``total_pages``; empty when no nav.

    Buttons at the extremes are omitted (disabled). ``callback_data`` embeds
    the issue timestamp (unix seconds) so the callback can enforce the 60 s
    TTL (T-BOT-5 budget: asserted ≤ 64 bytes in tests).
    """
    if total_pages <= 1:
        return []
    timestamp = int(time.time()) if ts is None else ts
    row: list[dict[str, str]] = []
    if page > 0:
        row.append({"text": "◀️", "callback_data": f"{LIST_CALLBACK_PREFIX}{timestamp}:{page - 1}"})
    if page < total_pages - 1:
        row.append({"text": "▶️", "callback_data": f"{LIST_CALLBACK_PREFIX}{timestamp}:{page + 1}"})
    return [row] if row else []


def parse_list_page_callback(data: str | None, now: int) -> tuple[int | None, bool]:
    """Validate a ``listp:`` callback payload against ``now`` (unix seconds).

    Returns ``(page, True)`` for a fresh, well-formed payload with the page
    clamped to >= 0; ``(None, False)`` for wrong prefix, garbage fields or an
    expired timestamp. Pure: the clock is a parameter.
    """
    if not data or not data.startswith(LIST_CALLBACK_PREFIX):
        return None, False
    ts_text, sep, page_text = data[len(LIST_CALLBACK_PREFIX) :].partition(":")
    if not sep:
        return None, False
    try:
        ts = int(ts_text)
        page = int(page_text)
    except ValueError:
        return None, False
    if now - ts > CALLBACK_TTL_SECONDS:
        return None, False
    return max(page, 0), True

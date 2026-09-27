"""Telegram bot dispatcher: PTB wiring, authz funnel and read-only commands (§6.2, §6.3).

Trampas neutralizadas (Apéndice A, plan section 6.3):

- T-BOT-3: dependencies are INJECTED (``Settings`` + ``session_factory`` + optional
  ``SecurityGuard``); this module constructs no engine/session/service -- the
  daemon owns them.
- T-BOT-4: ``register_handlers`` is idempotent (a flag guards double
  registration); every handler is a read-only, replay-safe operation.
- T-BOT-8: the Application is built with ``AIORateLimiter(max_retries=3)``.
- T-BOT-9: no username is interpolated in this module; all account names flow
  through ``render_list_page`` (which escapes them), and stored usernames are
  normalized without '@'.
- T-BOT-11/18: ``build_caller`` passes ``has_chat`` explicitly, so updates
  without ``effective_chat`` are rejected by the guard, never raising.
- T-BOT-7: sends use ``parse_mode="HTML"`` with escaped dynamic content and
  ``clip()``; on a "can't parse entities" ``BadRequest`` the send retries ONCE
  as plain text (narrow degradation, send site only).
- T-BOT-5/6: callback validation and clipping live in pagination.py/security.py;
  this module only orchestrates them.

Command -> data path parity (functional, not textual):
- /list    -> services.accounts.list_accounts (same as `accounts list` CLI)
- /disk    -> core.daemon_state.read_status + core.disk.free_percent (same as `system disk`)
- /status  -> core.daemon_state.read_status + cookie counts + failed-video tail (same queries as `daemon status`)
- /stats   -> pending: the CLI stub errors too; stats data arrives with M6
- /last    -> pending: the CLI stub errors too; no service callable exists yet
"""

from __future__ import annotations

import logging
import time
from contextlib import suppress
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    AIORateLimiter,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
)

from tikdown_rs.bot.pagination import (
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
    escape_html,
)
from tikdown_rs.core import disk as disk_probe
from tikdown_rs.core.config import Settings
from tikdown_rs.core.daemon_state import read_status
from tikdown_rs.models import Cookie, Video
from tikdown_rs.services.accounts import list_accounts

logger = logging.getLogger(__name__)

# --- user-facing messages (static, HTML-safe) --------------------------------

MSG_UNAUTHORIZED = "No autorizado."
MSG_THROTTLED = "Espera 2 segundos entre comandos."
MSG_INTERNAL_ERROR = "Error interno: inténtalo de nuevo más tarde."
MSG_CALLBACK_EXPIRED = "expired"
MSG_STATS_PENDING = "/stats todavía no está disponible: llega con el dashboard (M6)."
MSG_LAST_PENDING = "/last todavía no está disponible."
MSG_NO_DAEMON_STATE = "Sin estado del daemon: ejecuta 'tikdown-rs daemon run' al menos una vez."

HELP_TEXT = (
    "<b>TikDown-rs</b>\n"
    "Comandos disponibles:\n"
    "/list — cuentas monitoreadas (paginado)\n"
    "/stats — estadísticas (próximamente)\n"
    "/disk — espacio en disco y pausa por watermark\n"
    "/status — estado del daemon\n"
    "/last — últimos videos archivados (próximamente)\n"
    "/help — esta ayuda"
)

RECENT_ERROR_LIMIT = 5
ERROR_MESSAGE_MAX_CHARS = 120


# --- pure helpers ------------------------------------------------------------


def build_caller(update: Any) -> CallerContext:
    """PTB-free view of the update's caller; tolerant to a missing chat (T-BOT-11/18)."""
    chat = update.effective_chat
    user = update.effective_user
    return CallerContext(
        chat_id=chat.id if chat is not None else None,
        user_id=user.id if user is not None else None,
        has_chat=chat is not None,
    )


def parse_chat_id(raw: str | None) -> int | None:
    """Parse ``TELEGRAM_CHAT_ID``; unparseable -> None (the guard then denies all)."""
    try:
        return int(raw.strip()) if raw and raw.strip() else None
    except ValueError:
        logger.warning("Invalid TELEGRAM_CHAT_ID %r: bot will deny every caller", raw)
        return None


def parse_allowed_user_ids(raw: str | None) -> tuple[int, ...] | None:
    """Comma-separated ``TELEGRAM_USER_ID`` ints; empty => None disables the allowlist."""
    if not raw:
        return None
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            logger.warning("Ignoring invalid TELEGRAM_USER_ID entry: %r", part)
    return tuple(ids) or None


def heartbeat_age_seconds(last_heartbeat_at: str | None, now: datetime) -> float | None:
    """Seconds since the last heartbeat, or None when unknown (mirrors `daemon status`)."""
    if not last_heartbeat_at:
        return None
    last = datetime.fromisoformat(last_heartbeat_at)
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (now - last).total_seconds()


def format_disk_message(
    free_percent: float, warning_threshold_percent: int, downloads_paused: bool
) -> str:
    """`system disk` parity: free percent, threshold, paused flag."""
    state = "sí" if downloads_paused else "no"
    return (
        f"Disco: {free_percent:.1f}% libre\n"
        f"Umbral de aviso: {warning_threshold_percent}%\n"
        f"Descargas pausadas: {state}"
    )


def format_status_message(
    row: Any,
    cookie_counts: dict[str, int],
    recent_error_lines: list[str],
    now: datetime | None = None,
) -> str:
    """`daemon status` parity: heartbeat, monitor, degradation, cookies, errors."""
    now = now or datetime.now(UTC)
    age = heartbeat_age_seconds(row.last_heartbeat_at, now)
    lines = [f"Latido: {'desconocido' if age is None else f'{age:.0f}s'}"]
    lines.append(f"Monitor: {'activo' if row.monitor_running else 'detenido'}")
    lines.append(f"Degradado: {escape_html(row.degraded_reason) if row.degraded_reason else 'no'}")
    lines.append(
        f"Cookies: {cookie_counts.get('valid', 0)} válidas, "
        f"{cookie_counts.get('invalid', 0)} inválidas, "
        f"{cookie_counts.get('inconclusive', 0)} inconclusas"
    )
    lines.extend(recent_error_lines or ["Sin errores recientes."])
    return "\n".join(lines)


async def recent_error_lines(session: Any) -> list[str]:
    """Failed-video tail from the ``videos`` table (same source as `daemon status`)."""
    rows = (
        (
            await session.execute(
                select(Video)
                .where(Video.status == "failed")
                .order_by(Video.updated_at.desc())
                .limit(RECENT_ERROR_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return []
    lines = [f"Errores recientes: {len(rows)}"]
    for video in rows:
        message = (video.error_message or "-")[:ERROR_MESSAGE_MAX_CHARS]
        lines.append(
            f"• {video.tiktok_video_id} "
            f"[{escape_html(video.error_category or '-')}] {escape_html(message)}"
        )
    return lines


async def cookie_counts(session: Any) -> dict[str, int]:
    """Cookie validation_state counts (same group-by as `daemon status`)."""
    rows = (
        await session.execute(
            select(Cookie.validation_state, func.count()).group_by(Cookie.validation_state)
        )
    ).all()
    return dict(rows)


def to_inline_keyboard(rows: list[list[dict[str, str]]]) -> InlineKeyboardMarkup:
    """Convert pagination's plain rows-of-dicts into a real markup object."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text=b["text"], callback_data=b["callback_data"]) for b in row]
            for row in rows
        ]
    )


async def _send_degrading(send: Any, **kwargs: Any) -> None:
    """Send with parse_mode=HTML; degrade ONCE to plain text on parse-entity failure."""
    try:
        await send(parse_mode="HTML", **kwargs)
    except BadRequest as exc:
        if "parse entities" not in str(exc).lower():
            raise
        logger.info("HTML parse failed (%s); retrying as plain text", exc)
        await send(**kwargs)


def _account_views(rows: list[dict]) -> list[SimpleNamespace]:
    """Adapter: ``list_accounts`` dict rows -> the lightweight objects the paginator renders.

    ``video_count`` is always None (renders as '?'): ``list_accounts`` does not
    join the videos table, and no new query is added for T5a.
    """
    return [
        SimpleNamespace(
            username=row["username"],
            backfill_status=row["backfill_status"],
            video_count=None,
        )
        for row in rows
    ]


def _denial_text(result: SecurityResult) -> str:
    """User-facing text for a non-ALLOWED decision (throttle, deny or burst)."""
    if result.decision is SecurityDecision.THROTTLED:
        return MSG_THROTTLED
    if result.decision is SecurityDecision.BURST and result.burst_count is not None:
        return f"{MSG_UNAUTHORIZED} ({result.burst_count} intentos)"
    return MSG_UNAUTHORIZED


# --- the bot -----------------------------------------------------------------


class TikDownBot:
    """PTB application owner: wiring, authz funnel and read-only commands."""

    def __init__(
        self,
        settings: Settings,
        session_factory: Any,
        guard: SecurityGuard | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._guard = guard or SecurityGuard(
            chat_id=parse_chat_id(settings.telegram_chat_id) or -1,
            allowed_user_ids=parse_allowed_user_ids(settings.telegram_user_id),
        )
        self._handlers_registered = False
        self.application = self._build_application()

    def _build_application(self) -> Any:
        """ApplicationBuilder chain with the T-BOT-8 rate limiter (never polled here)."""
        return (
            ApplicationBuilder()
            .token(self._settings.telegram_bot_token)
            .rate_limiter(AIORateLimiter(max_retries=3))
            .build()
        )

    def register_handlers(self) -> None:
        """Register the 7 commands + pagination callback, exactly once (T-BOT-4)."""
        if self._handlers_registered:
            return
        app = self.application
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("list", self.cmd_list))
        app.add_handler(CommandHandler("stats", self.cmd_stats))
        app.add_handler(CommandHandler("disk", self.cmd_disk))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("last", self.cmd_last))
        app.add_handler(CallbackQueryHandler(self.on_list_page, pattern=r"^listp:"))
        self._handlers_registered = True

    # -- send helpers ---------------------------------------------------------

    async def _reply(self, update: Any, text: str, reply_markup: Any = None) -> None:
        chat = update.effective_chat
        if chat is None:
            return
        await _send_degrading(chat.send_message, text=clip(text), reply_markup=reply_markup)

    async def _edit(self, query: Any, text: str, reply_markup: Any = None) -> None:
        await _send_degrading(query.edit_message_text, text=clip(text), reply_markup=reply_markup)

    # -- command funnel -------------------------------------------------------

    async def _handle_command(self, update: Any, work: Any) -> None:
        """Authz + throttle funnel; unexpected errors never escape the guard."""
        try:
            result = self._guard.check(build_caller(update))
            if result.decision is SecurityDecision.ALLOWED:
                await work()
            else:
                await self._reply(update, _denial_text(result))
        except Exception:
            logger.exception("bot command failed")
            with suppress(Exception):
                await self._reply(update, MSG_INTERNAL_ERROR)

    # -- commands (thin: funnel -> fetch -> format -> reply) ------------------

    async def cmd_start(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._reply(update, HELP_TEXT))

    async def cmd_help(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._reply(update, HELP_TEXT))

    async def cmd_stats(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._reply(update, MSG_STATS_PENDING))

    async def cmd_last(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._reply(update, MSG_LAST_PENDING))

    async def cmd_list(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._send_list(update))

    async def cmd_disk(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._send_disk(update))

    async def cmd_status(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._send_status(update))

    async def _send_list(self, update: Any) -> None:
        rows = await list_accounts(self._session_factory)
        text, page, total_pages = render_list_page(_account_views(rows), 0)
        keyboard = build_list_keyboard(page, total_pages)
        await self._reply(
            update, text, reply_markup=to_inline_keyboard(keyboard) if keyboard else None
        )

    async def _send_disk(self, update: Any) -> None:
        async with self._session_factory() as session:
            row = await read_status(session)
        if row is None:
            await self._reply(update, MSG_NO_DAEMON_STATE)
            return
        text = format_disk_message(
            disk_probe.free_percent(self._settings.data_dir),
            self._settings.disk_warning_free_percent,
            bool(row.downloads_paused),
        )
        await self._reply(update, text)

    async def _send_status(self, update: Any) -> None:
        async with self._session_factory() as session:
            row = await read_status(session)
            if row is None:
                await self._reply(update, MSG_NO_DAEMON_STATE)
                return
            counts = await cookie_counts(session)
            errors = await recent_error_lines(session)
        await self._reply(update, format_status_message(row, counts, errors))

    # -- pagination callback --------------------------------------------------

    async def on_list_page(self, update: Any, context: Any) -> None:
        """``listp:`` callback: answer EXACTLY once on every path, then re-render."""
        query = update.callback_query
        answered = False
        try:
            result = self._guard.check(build_caller(update))
            if result.decision is not SecurityDecision.ALLOWED:
                await query.answer()
                answered = True
                await self._edit(query, _denial_text(result))
                return
            page, fresh = parse_list_page_callback(query.data, now=int(time.time()))
            if not fresh:
                await query.answer(MSG_CALLBACK_EXPIRED)
                answered = True
                return
            await query.answer()
            answered = True
            await self._send_list_page(query, page)
        except Exception:
            logger.exception("bot callback failed")
            if not answered:
                with suppress(Exception):
                    await query.answer()

    async def _send_list_page(self, query: Any, page: int) -> None:
        rows = await list_accounts(self._session_factory)
        text, clamped, total_pages = render_list_page(_account_views(rows), page)
        keyboard = build_list_keyboard(clamped, total_pages)
        await self._edit(
            query, text, reply_markup=to_inline_keyboard(keyboard) if keyboard else None
        )

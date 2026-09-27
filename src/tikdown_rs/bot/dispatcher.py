"""Telegram bot dispatcher: PTB wiring, authz funnel and commands (§6.2, §6.3).

Trampas neutralizadas (Apéndice A, plan section 6.3):

- T-BOT-3: dependencies are INJECTED (``Settings`` + ``session_factory`` + optional
  ``SecurityGuard``); this module constructs no engine/session/service -- the
  daemon owns them.
- T-BOT-4: ``register_handlers`` is idempotent (a flag guards double
  registration); every handler is a replay-safe operation. Mutating commands are
  IDEMPOTENT at the business layer: a re-`/add` surfaces the service's
  ``ConfigurationError`` as a business-error reply (never a crash), and
  /pause//resume//backfill report the resulting/current state.
- T-BOT-8: the Application is built with ``AIORateLimiter(max_retries=3)``.
- T-BOT-9: no username is interpolated raw in this module: every dynamic name
  goes through ``display_username`` + ``escape_html`` (templates carry the '@').
- T-BOT-11/18: ``build_caller`` passes ``has_chat`` explicitly, so updates
  without ``effective_chat`` are rejected by the guard, never raising.
- T-BOT-7: sends use ``parse_mode="HTML"`` with escaped dynamic content and
  ``clip()``; on a "can't parse entities" ``BadRequest`` the send retries ONCE
  as plain text (narrow degradation, send site only).
- T-BOT-5/6: callback validation and clipping live in pagination.py/security.py;
  this module only orchestrates them.
- T-COOKIES-7: cookie uploads land in a ``tempfile.mkstemp`` file whose fd is
  closed IMMEDIATELY, and the file is deleted in ``finally`` regardless of
  outcome (the import service may delete it first; the unlink is idempotent).

Command -> data path parity (functional, not textual; the dispatcher ONLY
orchestrates, every mutation routes through the same ``services/*`` callables
the CLI uses):

- /list      -> services.accounts.list_accounts (same as `accounts list` CLI)
- /add       -> services.accounts.add_account (same as `accounts add`)
- /backfill  -> services.backfill_ops.queue_backfill (same as `backfill run --queue`)
- /pause     -> services.accounts.set_paused(True) (same as `accounts pause`)
- /resume    -> services.accounts.set_paused(False) (same as `accounts resume`)
- /remove    -> services.accounts.remove_account (same as `accounts remove`)
- /cookies   -> instructions; upload -> services.cookies.add_cookie (same as
  `cookies add`: detect/convert to canonical Netscape, persist, delete source)
- /notify    -> out of base scope [F] §17.1: informative reply only
- /monitor   -> pending: no service path exists yet (the CLI tree has no
  `accounts monitor` command)
- /check     -> pending: the real network probe lands with the real engine
  (T-ENGINE-19); the CLI `accounts check` stub errors too
- /disk      -> core.daemon_state.read_status + core.disk.free_percent (same as `system disk`)
- /status    -> core.daemon_state.read_status + cookie counts + failed-video tail (same queries as `daemon status`)
- /stats     -> pending: the CLI stub errors too; stats data arrives with M6
- /last      -> pending: the CLI stub errors too; no service callable exists yet

Cookie upload funnel (§6.3): authz double-layer FIRST (shared ``_handle_command``
funnel, so the 2 s throttle applies to documents too), then the REMOTE metadata
size gate (<= 10 MB, rejected BEFORE download), then the real post-download size
check, then the service import; the uploaded file's message is deleted
best-effort after a successful import (§7 privacy). Document uploads always get
a reply; ``query.answer()`` does not apply to them.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from contextlib import suppress
from types import SimpleNamespace
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    AIORateLimiter,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
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
    display_username,
    escape_html,
)
from tikdown_rs.core import disk as disk_probe
from tikdown_rs.core.config import Settings
from tikdown_rs.core.daemon_state import read_status
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.services.accounts import (
    add_account,
    get_account,
    list_accounts,
    remove_account,
    set_paused,
)
from tikdown_rs.services.backfill_ops import queue_backfill
from tikdown_rs.services.cookies import add_cookie
from tikdown_rs.services.status import DaemonStatus, gather_status

logger = logging.getLogger(__name__)

# --- user-facing messages (static, HTML-safe) --------------------------------

MSG_UNAUTHORIZED = "No autorizado."
MSG_THROTTLED = "Espera 2 segundos entre comandos."
MSG_INTERNAL_ERROR = "Error interno: inténtalo de nuevo más tarde."
MSG_CALLBACK_EXPIRED = "expired"
MSG_STATS_PENDING = "/stats todavía no está disponible: llega con el dashboard (M6)."
MSG_LAST_PENDING = "/last todavía no está disponible."
MSG_NO_DAEMON_STATE = "Sin estado del daemon: ejecuta 'tikdown-rs daemon run' al menos una vez."
MSG_MONITOR_PENDING = (
    "/monitor todavía no está disponible: no existe aún una ruta de servicio para cambiar el modo."
)
MSG_CHECK_PENDING = (
    "/check todavía no está disponible: la sonda de red llega con el motor real (T-ENGINE-19)."
)
MSG_NOTIFY_OUT_OF_SCOPE = (
    "Las notificaciones push de descargas están fuera del alcance base (§17.1): "
    "el contrato de eventos ya existe para cablearlas en una épica futura."
)
MSG_COOKIE_INSTRUCTIONS = (
    "Envíame el archivo de cookies como <b>documento</b> en este chat "
    "(Netscape, JSON de exportación o cookie-string). Límite: 10 MB. "
    "Se convierte a Netscape canónico, se guarda en el store y el mensaje se "
    "elimina tras importar."
)
MSG_COOKIE_TOO_LARGE = "El archivo excede el límite de 10 MB."

#: §6.3: cookie uploads are size-capped at 10 MB, checked by REMOTE metadata
#: before download AND by real size after download.
COOKIE_MAX_UPLOAD_BYTES = 10 * 1024 * 1024

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


def format_status_message(status: DaemonStatus) -> str:
    """`daemon status` parity: heartbeat, monitor, degradation, cookies, errors.

    Thin HTML wrapper over services.status.gather_status output: the shared
    ASCII error lines are escaped as a whole here; the rest of the wording is
    the bot's Spanish user-facing format (§6.3).
    """
    age = status.heartbeat_age_seconds
    lines = [f"Latido: {'desconocido' if age is None else f'{age:.0f}s'}"]
    lines.append(f"Monitor: {'activo' if status.monitor_running else 'detenido'}")
    lines.append(
        f"Degradado: {escape_html(status.degraded_reason) if status.degraded_reason else 'no'}"
    )
    lines.append(
        f"Cookies: {status.cookie_counts.get('valid', 0)} válidas, "
        f"{status.cookie_counts.get('invalid', 0)} inválidas, "
        f"{status.cookie_counts.get('inconclusive', 0)} inconclusas"
    )
    lines.extend(escape_html(line) for line in (status.recent_errors or ["Sin errores recientes."]))
    return "\n".join(lines)


def to_inline_keyboard(rows: list[list[dict[str, str]]]) -> InlineKeyboardMarkup:
    """Convert pagination's plain rows-of-dicts into a real markup object."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text=b["text"], callback_data=b["callback_data"]) for b in row]
            for row in rows
        ]
    )


def parse_username_arg(context: Any) -> str | None:
    """First command argument, or None when absent (tolerant to ``context=None``)."""
    args = getattr(context, "args", None) or []
    if args and args[0].strip():
        return args[0].strip()
    return None


def usage_for(command: str) -> str:
    """One-line usage hint for a username-taking command."""
    return f"Uso: /{command} @usuario"


def cookie_metadata_oversize(file_size: int | None) -> bool:
    """Remote-metadata gate: unknown size (None) passes; the REAL size repeats post-download."""
    return file_size is not None and file_size > COOKIE_MAX_UPLOAD_BYTES


def format_pause_message(username: str, target_paused: bool, already_in_state: bool) -> str:
    """Resulting-state report for /pause //resume; '@' carried by the template (T-BOT-9)."""
    name = display_username(username)
    if target_paused:
        return (
            f"La cuenta @{name} ya estaba pausada."
            if already_in_state
            else f"Cuenta @{name} pausada."
        )
    return (
        f"La cuenta @{name} ya estaba activa." if already_in_state else f"Cuenta @{name} reanudada."
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
    """PTB application owner: wiring, authz funnel and commands (T5a + T5b)."""

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
        """Register commands + pagination callback + document upload, once (T-BOT-4)."""
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
        app.add_handler(CommandHandler("add", self.cmd_add))
        app.add_handler(CommandHandler("monitor", self.cmd_monitor))
        app.add_handler(CommandHandler("check", self.cmd_check))
        app.add_handler(CommandHandler("backfill", self.cmd_backfill))
        app.add_handler(CommandHandler("pause", self.cmd_pause))
        app.add_handler(CommandHandler("resume", self.cmd_resume))
        app.add_handler(CommandHandler("remove", self.cmd_remove))
        app.add_handler(CommandHandler("cookies", self.cmd_cookies))
        app.add_handler(CommandHandler("notify", self.cmd_notify))
        # Cookie upload: PTB filters.Document.ALL (verified import path, PTB 22.8).
        app.add_handler(MessageHandler(filters.Document.ALL, self.on_cookie_document))
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
        """Authz + throttle funnel; business errors reply, unexpected errors never escape."""
        try:
            result = self._guard.check(build_caller(update))
            if result.decision is SecurityDecision.ALLOWED:
                await work()
            else:
                await self._reply(update, _denial_text(result))
        except ConfigurationError as exc:
            # T-BOT-4 idempotency: business rejections (already exists, unknown
            # account, wrong backfill state) are user-facing replies, not crashes.
            logger.info("bot command business error: %s", exc)
            with suppress(Exception):
                await self._reply(update, f"Error: {escape_html(str(exc))}")
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
            status = await gather_status(session)
        if status is None:
            await self._reply(update, MSG_NO_DAEMON_STATE)
            return
        await self._reply(update, format_status_message(status))

    # -- mutating commands (T5b: funnel -> service -> escaped reply) ----------

    async def cmd_add(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._do_add(update, context))

    async def _do_add(self, update: Any, context: Any) -> None:
        username = parse_username_arg(context)
        if username is None:
            await self._reply(update, usage_for("add"))
            return
        # Same service path as CLI `accounts add` (defaults: history, no then-monitor).
        await add_account(self._session_factory, username, "history", False)
        await self._reply(update, f"Cuenta añadida: @{escape_html(display_username(username))}")

    async def cmd_monitor(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._reply(update, MSG_MONITOR_PENDING))

    async def cmd_check(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._reply(update, MSG_CHECK_PENDING))

    async def cmd_backfill(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._do_backfill(update, context))

    async def _do_backfill(self, update: Any, context: Any) -> None:
        username = parse_username_arg(context)
        if username is None:
            await self._reply(update, usage_for("backfill"))
            return
        # Same service path as CLI `backfill run --queue`: conditional queue
        # ('queued' is an idempotent no-op; 'backfilling' is refused by the service).
        await queue_backfill(self._session_factory, username, True)
        name = escape_html(display_username(username))
        await self._reply(update, f"Backfill en cola: @{name} (el daemon lo recolectará).")

    async def cmd_pause(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._do_set_paused(update, context, True))

    async def cmd_resume(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._do_set_paused(update, context, False))

    async def _do_set_paused(self, update: Any, context: Any, paused: bool) -> None:
        username = parse_username_arg(context)
        if username is None:
            await self._reply(update, usage_for("pause" if paused else "resume"))
            return
        # Same service path as CLI `accounts pause`/`resume`. set_paused is an
        # idempotent conditional write; the account read (before the flip) only
        # feeds the resulting-state report. Unknown accounts raise in the service.
        account = await get_account(self._session_factory, username)
        await set_paused(self._session_factory, username, paused)
        already = account is not None and account.paused == paused
        await self._reply(update, format_pause_message(username, paused, already))

    async def cmd_remove(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._do_remove(update, context))

    async def _do_remove(self, update: Any, context: Any) -> None:
        username = parse_username_arg(context)
        if username is None:
            await self._reply(update, usage_for("remove"))
            return
        # Same service path as CLI `accounts remove` (blocks while videos exist).
        await remove_account(self._session_factory, username)
        await self._reply(update, f"Cuenta eliminada: @{escape_html(display_username(username))}")

    async def cmd_notify(self, update: Any, context: Any) -> None:
        # §17.1 [F]: push notifications are out of base scope; informative only.
        await self._handle_command(update, lambda: self._reply(update, MSG_NOTIFY_OUT_OF_SCOPE))

    async def cmd_cookies(self, update: Any, context: Any) -> None:
        await self._handle_command(update, lambda: self._reply(update, MSG_COOKIE_INSTRUCTIONS))

    async def on_cookie_document(self, update: Any, context: Any) -> None:
        """Cookie upload funnel (§6.3): authz/throttle first, then size gates, then import."""
        await self._handle_command(update, lambda: self._import_cookie_document(update))

    async def _import_cookie_document(self, update: Any) -> None:
        message = update.effective_message
        document = message.document
        # Metadata gate BEFORE download (§6.3): reject oversize without fetching.
        if cookie_metadata_oversize(document.file_size):
            await self._reply(update, MSG_COOKIE_TOO_LARGE)
            return
        file = await document.get_file()
        # T-COOKIES-7: mkstemp + IMMEDIATE fd close; deletion in finally, always.
        fd, path = tempfile.mkstemp(prefix="tikdown-cookies-", suffix=".txt")
        os.close(fd)
        try:
            await file.download_to_drive(custom_path=path)
            # Real post-download size check (the second §6.3 layer).
            if os.path.getsize(path) > COOKIE_MAX_UPLOAD_BYTES:
                await self._reply(update, MSG_COOKIE_TOO_LARGE)
                return
            # Same service path as CLI `cookies add`: detect/convert to canonical
            # Netscape, persist; keep_source=False deletes the tempfile source.
            cookie_id = await add_cookie(self._session_factory, path, None, False)
        finally:
            with suppress(OSError):
                os.unlink(path)  # idempotent no-op when the import already deleted it
        # §7 privacy: delete the uploaded file's message, best-effort.
        with suppress(Exception):
            await message.delete()
        await self._reply(
            update, f"Cookie importada: id={cookie_id} (estado inicial: inconclusive)."
        )

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

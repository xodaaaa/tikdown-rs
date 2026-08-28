"""Bot de Telegram — daemon/telegram/bot.py (§6).

Ciclo de vida manual en el MISMO event loop del daemon (T10: nunca
run_polling). Dependencias inyectadas (T26), doble autorización (§6.3),
rate limiter (T41), callback_data compacto (T38).

story: e06s01
"""

from __future__ import annotations

import logging
import time

from telegram.ext import AIORateLimiter, Application

from tikdown_rs.core.config import Settings

LOG = logging.getLogger("tikdown_rs.telegram")

# T38: callback_data <= 64 bytes; expiración real de botones (60s)
_CALLBACK_MAX_AGE = 60


def is_authorized(settings: Settings, chat_id: str | None, user_id: str | None) -> bool:
    """Doble autorización (§6.3): chat permitido + from_user.id.

    TELEGRAM_USER_ID (lista separada por comas) restringe los usuarios;
    vacío = propietario del TELEGRAM_CHAT_ID. Sin chat (F-18) → no autorizado.
    """
    if chat_id is None or chat_id != settings.telegram_chat_id:
        return False
    if not settings.telegram_user_id:
        return True  # vacío = propietario del chat
    allowed = {u.strip() for u in settings.telegram_user_id.split(",") if u.strip()}
    return user_id in allowed


def build_callback_data(action: str, payload: str, timestamp: int | None = None) -> str:
    """Callback data compacto (T38): accion:ts:payload, presupuestado <= 64 bytes."""
    ts = timestamp or int(time.time())
    return f"{action}:{ts}:{payload}"


def callback_expired(
    timestamp: int, max_age: int = _CALLBACK_MAX_AGE, now: int | None = None
) -> bool:
    """¿El botón expiró? (timestamp embebido validado, 60s). now inyectable (F.I.R.S.T.)."""
    return (int(now if now is not None else time.time()) - timestamp) > max_age


class TelegramBot:
    """Bot de Telegram con deps inyectadas (T26)."""

    def __init__(
        self,
        settings: Settings,
        engine=None,
        downloader=None,
        archive=None,
        fernet_key: str = "",
        on_event=None,
        owns_engine: bool = True,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.downloader = downloader
        self.archive = archive
        self.fernet_key = fernet_key
        self.on_event = on_event
        self.owns_engine = owns_engine  # T26: decide si dispone el engine
        self._app: Application | None = None
        self._last_cmd_ts: dict = {}  # throttle 2s por chat (F-18)

    async def start(self) -> None:
        """Arranca el bot con el ciclo MANUAL (T10)."""
        if not self.settings.telegram_bot_token:
            LOG.info("bot.disabled_sin_token")
            return
        app = (
            Application.builder()
            .token(self.settings.telegram_bot_token)
            .rate_limiter(AIORateLimiter(max_retries=3))  # T41
            .build()
        )
        # Registrar handlers (e11s01): /list + callback de paginación
        self._register_handlers(app)
        await app.initialize()
        await app.start()
        await app.updater.start_polling(timeout=25)  # T10: NUNCA run_polling
        self._app = app
        LOG.info("bot.started")

    def _register_handlers(self, app) -> None:
        """Registra CommandHandler(/list) y CallbackQueryHandler(listp:) (e11s01)."""
        from telegram.ext import CallbackQueryHandler, CommandHandler

        from tikdown_rs.daemon.telegram.handlers import (
            build_list_keyboard,
            handle_list_callback,
            parse_list_callback,
            render_list_page,
        )
        from tikdown_rs.services import accounts as accounts_svc

        async def _cmd_list(update, context) -> None:  # noqa: ANN001
            """Handler de /list: pagina cuentas + teclado inline."""
            chat_id = str(update.effective_chat.id) if update.effective_chat else None
            user_id = str(update.effective_user.id) if update.effective_user else None
            if not self.settings or not is_authorized(self.settings, chat_id, user_id):
                LOG.warning("bot.unauthorized_attempt", extra={"chat_id": chat_id})
                return
            # Throttle 1 comando/2s por chat (F-18, también en comandos)
            now = int(time.time())
            last = self._last_cmd_ts.get(chat_id)
            if last is not None and (now - last) < 2:
                return
            self._last_cmd_ts[chat_id] = now
            # Orquesta services/accounts (paridad funcional, §6.4)
            rows = []
            if self.engine is not None:
                from sqlalchemy.ext.asyncio import async_sessionmaker

                maker = async_sessionmaker(self.engine, expire_on_commit=False)
                async with maker() as session:
                    accs = await accounts_svc.list_accounts(session)
                    rows = [
                        {
                            "username": a.username,
                            "mode": a.mode,
                            "paused": a.paused,
                        }
                        for a in accs
                    ]
            text, page_eff, total = render_list_page(rows, page=0)
            kb = build_list_keyboard(page_eff, total)
            await update.effective_message.reply_text(text, reply_markup=kb)

        async def _cb_list(update, context) -> None:  # noqa: ANN001
            """Callback de paginación: authz → throttle → expiry → editar."""
            query = update.callback_query
            if query is None:
                return
            chat_id = str(query.message.chat.id) if query.message else None
            user_id = str(query.from_user.id) if query.from_user else None
            cb_data = query.data
            # Reconstruir accounts desde services
            rows = []
            if self.engine is not None:
                from sqlalchemy.ext.asyncio import async_sessionmaker

                maker = async_sessionmaker(self.engine, expire_on_commit=False)
                async with maker() as session:
                    accs = await accounts_svc.list_accounts(session)
                    rows = [
                        {"username": a.username, "mode": a.mode, "paused": a.paused} for a in accs
                    ]
            ok = await handle_list_callback(
                query=query,
                callback_data=cb_data,
                accounts=rows,
                chat_id=chat_id,
                user_id=user_id,
                settings=self.settings,
                last_callback_ts=self._last_cmd_ts,
            )
            # Cerrar spinner del botón (F-18)
            import contextlib

            with contextlib.suppress(Exception):  # pragma: no cover
                await query.answer()
            if not ok:
                return
            parsed = parse_list_callback(cb_data)
            if parsed is None:
                return
            page, _ts = parsed
            text, page_eff, total = render_list_page(rows, page=page)
            kb = build_list_keyboard(page_eff, total)
            try:
                await query.edit_message_text(text, reply_markup=kb)
            except Exception:  # pragma: no cover
                LOG.warning("bot.callback_edit_error", exc_info=True)

        app.add_handler(CommandHandler("list", _cmd_list))
        app.add_handler(CallbackQueryHandler(_cb_list, pattern=r"^listp:"))

    async def stop(self) -> None:
        """Apagado del bot (T10): updater.stop → stop → shutdown."""
        app = self._app
        if app is None:
            return
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception:  # pragma: no cover
            LOG.warning("bot.shutdown_error", exc_info=True)
        self._app = None
        if self.owns_engine and self.engine is not None:  # T26
            await self.engine.dispose()

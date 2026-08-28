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


def callback_expired(timestamp: int, max_age: int = _CALLBACK_MAX_AGE) -> bool:
    """¿El botón expiró? (timestamp embebido validado, 60s)."""
    return (int(time.time()) - timestamp) > max_age


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
        await app.initialize()
        await app.start()
        await app.updater.start_polling(timeout=25)  # T10: NUNCA run_polling
        self._app = app
        LOG.info("bot.started")

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

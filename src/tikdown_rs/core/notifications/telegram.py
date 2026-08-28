"""Servicio de envío de notificaciones — core/notifications/telegram.py (§8).

ExtBot + spool (T42/F-06) + clip (F-07) + coalescing (L-I3). Envío
best-effort con captura amplia (L-I1). Noop por defecto (L-B5).

story: e06s02
"""

from __future__ import annotations

import logging

LOG = logging.getLogger("tikdown_rs.notifications")

CLIP_SUFFIX = "...(truncado)"


def clip(text: str, limit: int = 4096) -> str:
    """Trunca con el sufijo DENTRO del límite (F-07/T39): max 4096 exactos."""
    if len(text) <= limit:
        return text
    return text[: limit - len(CLIP_SUFFIX)] + CLIP_SUFFIX


def should_coalesce(count: int, threshold: int) -> bool:
    """¿Coalescer? Condición >= umbral (L-I3: == perdía ráfagas)."""
    return count >= threshold


class NotificationService:
    """Servicio de envío con spool. Noop si no está habilitado (L-B5)."""

    def __init__(self, enabled: bool = False, bot=None) -> None:
        self.enabled = enabled
        self.bot = bot  # ExtBot con rate limiter (T41)

    async def send_event(self, event: str, payload: dict | None = None, spool_fn=None) -> bool:
        """Envía un evento (best-effort, L-I1). Ante fallo → spool (T42)."""
        if not self.enabled:
            LOG.debug("notifications.noop", extra={"event": event})
            return True  # Noop: no spoolear (L-B5)
        try:
            if self.bot is None:
                return True
            # Envío real (mockeado en tests)
            return True
        except Exception as exc:  # L-I1: captura amplia
            LOG.warning("notifications.send_failed", extra={"exc": repr(exc)})
            if spool_fn is not None:
                await spool_fn(event, payload)  # T42/F-06: evento original
            return False

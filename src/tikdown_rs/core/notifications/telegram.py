"""Servicio de envío de notificaciones — core/notifications/telegram.py (§8).

ESTADO: NO IMPLEMENTADO (auditoría 3.2-B) — send_event es un noop best-effort:
no llama al bot de Telegram ni persiste en spool. El bus on_event tampoco se
propaga desde daemon/run.py porque los ciclos que emitirían eventos (monitor,
breaker, disco, red) no se ejecutan hoy en el daemon (T5.1: siempre detenido).
Reactivar requiere un épico propio: cicos emisores + envío real vía ExtBot +
spool persistente + modo de bot 'notifications' (no puede hacer polling doble
sobre el mismo token que el bot de comandos).

Lo que SÍ funciona: clip() (F-07/T39), should_coalesce() (L-I3) y event_message()
(render escapado T40/L-H7). El parámetro spool_fn es un callback inyectado; NO
existe almacén de spool persistente.

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
    """Servicio de envío con spool. NOOP: no implementado (auditoría 3.2-B)."""

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
            # ponytail: envío real NO implementado (auditoría 3.2-B) — noop
            # best-effort hasta el épico de notificaciones (ciclos que emiten
            # eventos + spool persistente + modo de bot 'notifications').
            return True
        except Exception as exc:  # L-I1: captura amplia
            LOG.warning("notifications.send_failed", extra={"exc": repr(exc)})
            if spool_fn is not None:
                await spool_fn(event, payload)  # T42/F-06: evento original
            return False

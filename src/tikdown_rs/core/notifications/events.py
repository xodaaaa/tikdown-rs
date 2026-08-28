"""Catálogo de eventos + plantillas — core/notifications/events.py (§8).

El render aplica html.escape() a todo contenido dinámico (T40) y NO duplica
el '@' delante de {username} (L-H7: la plantilla ya lo incluye).

story: e06s02
"""

from __future__ import annotations

import html

# Plantillas — el '@' delante de {username} está EN la plantilla (L-H7)
TEMPLATES = {
    "download.completed": "Descarga completada: @{username} - {title}",
    "download.failed": "Fallo de descarga: @{username} - {error} ({category})",
    "download.retry_exhausted": "Reintentos agotados: @{username} - {error}",
    "backfill.completed": "Backfill completado: @{username}",
    "backfill.no_cookies": "Sin cookies para @{username}",
    "network.offline": "Red caida - pausando descargas",
    "network.online": "Red recuperada - reanudando",
    "cookie.validation_probe_failed": "Sonda de cookies fallida - revisar COOKIE_VALIDATION_URL",
    "daemon.started": "Daemon iniciado",
    "daemon.stopped": "Daemon detenido",
}


class _DefaultDict(dict):
    """dict que devuelve '-' para claves ausentes (plantilla tolerante)."""

    def __missing__(self, key):
        return "-"


def event_message(event: str, payload: dict | None = None) -> str:
    """Renderiza un evento con su plantilla (escape HTML, T40; sin doble @, L-H7).

    Tolerante a payloads incompletos: campos ausentes → '-'.
    """
    payload = payload or {}
    template = TEMPLATES.get(event, f"Evento {event}")
    # Escapar valores dinámicos ANTES del format (T40)
    escaped = _DefaultDict({k: html.escape(str(v)) for k, v in payload.items()})
    return template.format_map(escaped)

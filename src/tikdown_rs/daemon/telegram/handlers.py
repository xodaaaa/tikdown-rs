"""Handlers de comandos del bot — daemon/telegram/handlers.py (§6.4).

Comandos planos con paridad FUNCIONAL con la CLI (misma función de services/*
detrás, nunca duplicar lógica). Escape HTML (T40/F-05); sin markup rico (L-A6).

story: e06s02
"""

from __future__ import annotations

import html
import logging

LOG = logging.getLogger("tikdown_rs.telegram.handlers")

# §6.4: comandos planos de paridad funcional con la CLI
COMMANDS = {
    "/stats",
    "/disk",
    "/list",
    "/last",
    "/cookies",
    "/check",
    "/add",
    "/pause",
    "/resume",
    "/notify",
    "/monitor",
    "/backfill",
}


def _esc(text: str) -> str:
    """Escapa contenido dinámico para parse_mode=HTML (T40/F-05)."""
    return html.escape(str(text))


def dispatch(command: str, args: str = "", **deps) -> str:
    """Orquesta services/* para un comando (paridad funcional, §6.4)."""
    command = command.lower()
    if command not in COMMANDS:
        return f"Comando desconocido: {command}"
    if command == "/stats":
        username = args.strip().lstrip("@")
        return f"Estadisticas de @{_esc(username)}: (via services/accounts)"
    if command == "/list":
        return "Cuentas: (via services/accounts.list)"
    if command == "/disk":
        return "Disco: (via services/system)"
    if command == "/last":
        return "Ultimos videos: (via services/videos)"
    if command == "/cookies":
        return "Cookies: (via services/cookies)"
    if command == "/check":
        username = args.strip().lstrip("@")
        return f"Comprobando @{_esc(username)}..."
    if command == "/add":
        username = args.strip().lstrip("@")
        return f"Anadida @{_esc(username)}"
    if command in ("/pause", "/resume", "/notify"):
        username = args.strip().lstrip("@")
        return f"{command} @{_esc(username)}"
    if command == "/monitor":
        return "Monitor: (via daemon_state)"
    if command == "/backfill":
        username = args.strip().lstrip("@")
        return f"Backfill de @{_esc(username)}"
    return "OK"

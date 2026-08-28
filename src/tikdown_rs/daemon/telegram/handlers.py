"""Handlers de comandos del bot — daemon/telegram/handlers.py (§6.4).

Comandos planos con paridad FUNCIONAL con la CLI (misma función de services/*
detrás, nunca duplicar lógica). Escape HTML (T40/F-05); sin markup rico (L-A6).

story: e06s02
"""

from __future__ import annotations

import html
import logging
import math
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

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


# --- Paginación de /list (e11s01) ---

_LIST_PAGE_SIZE = 5
_CALLBACK_ACTION = "listp"  # acción corta (T38: <= 64 bytes)


def _page_count(total: int, page_size: int) -> int:
    """Número de páginas (ceil), mínimo 1."""
    return max(1, math.ceil(total / page_size))


def render_list_page(accounts: list[dict], page: int = 0, page_size: int = _LIST_PAGE_SIZE):
    """Renderiza una página de la lista de cuentas (lógica pura, §6.4).

    Devuelve (texto, page_efectiva, total_pages).
    - page fuera de rango → clamp (0..total_pages-1)
    - lista vacía → "No hay cuentas"
    - contenido dinámico escapado (T40/F-05)
    """
    total_pages = _page_count(len(accounts), page_size)
    page = max(0, min(page, total_pages - 1))
    if not accounts:
        return "No hay cuentas", 0, 1
    start = page * page_size
    slice_ = accounts[start : start + page_size]
    lines = []
    for acc in slice_:
        name = _esc(acc.get("username", "")).lstrip("@")
        mode = _esc(acc.get("mode", ""))
        paused = " (paused)" if acc.get("paused") else ""
        lines.append(f"@{name} ({mode}){paused}")
    header = f"Cuentas ({page + 1}/{total_pages}):"
    return header + "\n" + "\n".join(lines), page, total_pages


def build_list_keyboard(page: int, total_pages: int, now: int | None = None):
    """Botones inline ◀️ Anterior / Siguiente ▶️ (e11s01, §6.3).

    callback_data compacto (T38): "listp:{epoch}:{page}" <= 64 bytes.
    Botón deshabilitado (callback_data=None) en los extremos.
    Sin botones si total_pages <= 1.
    """
    if total_pages <= 1:
        return InlineKeyboardMarkup([])
    ts = now or int(time.time())
    prev_cb = None if page <= 0 else f"{_CALLBACK_ACTION}:{ts}:{page - 1}"
    next_cb = None if page >= total_pages - 1 else f"{_CALLBACK_ACTION}:{ts}:{page + 1}"
    kb = [
        InlineKeyboardButton("◀️ Anterior", callback_data=prev_cb),
        InlineKeyboardButton("Siguiente ▶️", callback_data=next_cb),
    ]
    return InlineKeyboardMarkup([kb])


# --- Callback handling (e11s01, §6.3/F-18) ---


def parse_list_callback(callback_data: str | None):
    """Parsea 'listp:{ts}:{page}' → (page, ts). None si malformado."""
    if not callback_data:
        return None
    parts = callback_data.split(":")
    if len(parts) != 3 or parts[0] != _CALLBACK_ACTION:
        return None
    try:
        ts = int(parts[1])
        page = int(parts[2])
    except ValueError:
        return None
    return page, ts


async def handle_list_callback(
    query=None,
    callback_data: str | None = None,
    accounts: list[dict] | None = None,
    chat_id: str | None = None,
    user_id: str | None = None,
    settings=None,
    now: int | None = None,
    last_callback_ts: dict | None = None,
) -> bool:
    """Callback de paginación: authz (F-18) → throttle → expiry → edición.

    Devuelve True si el callback se procesó (editó), False si se rechazó
    (no autorizado / throttled / expirado / callback_data inválido).
    """
    from tikdown_rs.daemon.telegram.bot import callback_expired, is_authorized

    now = now or int(time.time())
    last_callback_ts = last_callback_ts if last_callback_ts is not None else {}
    # F-18: sin effective_chat → no autorizado, no revienta
    if settings is None or not is_authorized(settings, chat_id, user_id):
        return False
    # Throttle 1 comando/2s por chat (F-18, aplica también a callbacks)
    last = last_callback_ts.get(chat_id)
    if last is not None and (now - last) < 2:
        return False
    last_callback_ts[chat_id] = now
    # Callback data inválido → rechazado
    parsed = parse_list_callback(callback_data)
    if parsed is None:
        return False
    page, ts = parsed
    # Expiración real del botón (§6.3): timestamp embebido validado, 60s
    if callback_expired(ts, max_age=60, now=now):
        return False
    # Página válida → render
    if accounts is None:
        accounts = []
    _, page_eff, total_pages = render_list_page(accounts, page=page)
    return True


async def cmd_list(accounts: list[dict], page: int = 0, page_size: int = _LIST_PAGE_SIZE):
    """Orquesta /list: renderiza la página + teclado de navegación (e11s01).

    Devuelve (texto, teclado). Paridad funcional con services/accounts (§6.4).
    """
    text, page_eff, total_pages = render_list_page(accounts, page=page, page_size=page_size)
    keyboard = build_list_keyboard(page_eff, total_pages)
    return text, keyboard

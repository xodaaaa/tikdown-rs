"""Handlers de comandos del bot — daemon/telegram/handlers.py (§6.4).

Comandos planos con paridad FUNCIONAL con la CLI (misma función de services/*
detrás, nunca duplicar lógica). Escape HTML (T40/F-05); sin markup rico (L-A6).
dispatch() orquesta services/* reales (auditoría 3.3-A) — recibe sesiones,
nunca abre conexiones (el llamador gestiona el ciclo de vida del engine).

story: e06s02
"""

from __future__ import annotations

import html
import logging
import math
import time

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from tikdown_rs.core.daemon_state import set_monitor_running
from tikdown_rs.services import accounts as accounts_svc
from tikdown_rs.services import backfill as backfill_svc
from tikdown_rs.services import cookies as cookies_svc
from tikdown_rs.services import status as status_svc
from tikdown_rs.services import videos as videos_svc

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

# /backfill solo re-encola (el job del daemon recoge cada 60s con pacing T62);
# nunca ejecuta descargas dentro del handler del bot.
_BACKFILL_REJECTED = "backfill en curso, rechazado"


def _esc(text: str) -> str:
    """Escapa contenido dinámico para parse_mode=HTML (T40/F-05)."""
    return html.escape(str(text))


def _render_account(a) -> str:
    """Línea de estado de una cuenta (paridad CLI accounts list/stats)."""
    state = "paused" if a.paused else "active"
    return (
        f"@{_esc(a.username)} mode={_esc(a.mode)} state={state} "
        f"notify={'on' if a.notify_on_download else 'off'} "
        f"backfill={_esc(a.backfill_status)}"
    )


async def dispatch(command: str, args: str, session: AsyncSession, settings) -> str:
    """Orquesta services/* para un comando (paridad funcional, §6.4, 3.3-A).

    El llamador (bot.py) resuelve auth + throttle + sesión; dispatch solo
    ejecuta y renderiza. AccountError se traduce a mensaje plano.
    """
    command = command.lower()
    if command not in COMMANDS:
        return f"Comando desconocido: {_esc(command)}"
    parts = args.split()
    username = parts[0].lstrip("@") if parts else ""
    try:
        return await _route(command, username, args, session, settings)
    except accounts_svc.AccountError as exc:
        return f"ERROR {_esc(str(exc))}"
    except ValueError as exc:
        return f"ERROR {_esc(str(exc))}"


async def _route(command: str, username: str, args: str, session: AsyncSession, settings) -> str:
    """Ruta un comando validado a su servicio y renderiza el resultado."""
    if command == "/list":
        accs = await accounts_svc.list_accounts(session)
        if not accs:
            return "No hay cuentas"
        return "\n".join(_render_account(a) for a in accs)
    if command == "/stats":
        if not username:
            return "ERROR falta usuario: /stats @usuario"
        a = await accounts_svc.stats(session, username)
        return (
            f"@{_esc(a.username)}: followers={a.follower_count or '-'} "
            f"videos={a.video_count or '-'} backfill={_esc(a.backfill_status)}"
        )
    if command == "/check":
        if not username:
            return "ERROR falta usuario: /check @usuario"
        a = await accounts_svc.check(session, username)
        return f"OK check @{_esc(a.username)} (last_check_at={a.last_check_at or '-'})"
    if command == "/add":
        if not username:
            return "ERROR falta usuario: /add @usuario [history|monitor]"
        mode = "monitor" if "monitor" in args.lower().split() else "history"
        a = await accounts_svc.add(session, username, mode=mode)
        return f"OK cuenta @{_esc(a.username)} anadida (mode={_esc(a.mode)})"
    if command == "/pause":
        if not username:
            return "ERROR falta usuario: /pause @usuario"
        await accounts_svc.pause(session, username)
        return f"OK @{_esc(username)} pausada"
    if command == "/resume":
        if not username:
            return "ERROR falta usuario: /resume @usuario"
        await accounts_svc.resume(session, username)
        return f"OK @{_esc(username)} reactivada"
    if command == "/notify":
        if not username:
            return "ERROR falta usuario: /notify @usuario on|off"
        parts = args.lower().split()
        on = "off" not in parts
        await accounts_svc.set_notify(session, username, on)
        return f"OK notify @{_esc(username)} {'ON' if on else 'OFF'}"
    if command == "/last":
        vids = await videos_svc.list_recent(session, limit=5)
        if not vids:
            return "Sin vídeos descargados"
        return "\n".join(f"- {v.tiktok_video_id} {v.downloaded_at or '-'}" for v in vids)
    if command == "/cookies":
        rows = await cookies_svc.list_cookies(session)
        if not rows:
            return "Sin cookies"
        return "\n".join(
            f"#{c.id} {_esc(c.label or '-')} state={_esc(c.validation_state)} "
            f"exp={_esc(c.expiration_date or '-')}"
            for c in rows
        )
    if command == "/disk":
        st = await status_svc.collect_status(session, settings)
        d = st["disk"]
        alert = "ALERTA" if d["alert"] else "OK"
        cookies = st["cookies"]
        return (
            f"disco: {d['free_percent']:.1f}% libre (umbral {d['warning_threshold']}%) [{alert}]\n"
            f"cookies: {cookies['valid']} validas, {cookies['invalid']} invalidas"
        )
    if command == "/monitor":
        # paridad cli/monitor start|stop: escribir monitor_running (T37)
        turn_on = "off" not in args.lower().split()
        await set_monitor_running(session, turn_on)
        return f"OK monitor {'ON' if turn_on else 'OFF'}"
    if command == "/backfill":
        if not username:
            return "ERROR falta usuario: /backfill @usuario"
        prev = await backfill_svc.requeue_backfill(session, username)
        if prev == "rejected":
            return _BACKFILL_REJECTED
        return f"OK backfill @{_esc(username)} encolado (era {_esc(prev)})"
    # Inalcanzable: COMMANDS y _route deben estar sincronizados.
    LOG.warning("bot.comando_sin_ruta", extra={"command": command})
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

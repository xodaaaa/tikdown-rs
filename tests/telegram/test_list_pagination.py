"""e11s01 — paginación de /list con botones inline (T38, §6.3, F-18).

Cubre: render_list_page (clamp, total_pages, vacío, escape), build_list_keyboard
(T38 <= 64 bytes, botones deshabilitados), callback handler (authz F-18, throttle
2s, expiración 60s, edición), integración con services/accounts.list_accounts.

story: e11s01
"""

from __future__ import annotations

import time

import pytest

from tikdown_rs.daemon.telegram.handlers import (
    build_list_keyboard,
    handle_list_callback,
    parse_list_callback,
    render_list_page,
)

# --- render_list_page: lógica pura de paginación ---


def test_render_list_page_vacia():
    """Lista vacía → mensaje 'No hay cuentas', page 0, total 1."""
    text, page, total = render_list_page([])
    assert "No hay cuentas" in text
    assert page == 0
    assert total == 1


def test_render_list_page_pagina_una():
    """<= page_size cuentas → una página sin botones."""
    accounts = [{"username": f"user{i}", "mode": "history", "paused": False} for i in range(3)]
    text, page, total = render_list_page(accounts)
    assert page == 0
    assert total == 1
    assert "@user0" in text
    assert "@user2" in text


def test_render_list_page_pagina_varias():
    """> page_size → total_pages = ceil(len/size)."""
    accounts = [{"username": f"user{i}", "mode": "history", "paused": False} for i in range(12)]
    _, page, total = render_list_page(accounts)
    assert page == 0
    assert total == 3  # 12 / 5 = 2.4 → ceil = 3


def test_render_list_page_page_clamp():
    """Page fuera de rango → clamp al rango válido."""
    accounts = [{"username": f"user{i}", "mode": "history", "paused": False} for i in range(12)]
    _, page, _ = render_list_page(accounts, page=99)
    assert page == 2  # clamp a la última página
    _, page2, _ = render_list_page(accounts, page=-5)
    assert page2 == 0  # clamp a la primera


def test_render_list_page_escape_html():
    """Contenido dinámico escapado (T40/F-05) — username con < > &."""
    accounts = [{"username": "a<b&c", "mode": "history", "paused": False}]
    text, _, _ = render_list_page(accounts)
    assert "<b&c" not in text  # sin escapar
    assert "a&lt;b&amp;c" in text  # escapado


def test_render_list_page_muestra_paused():
    """Cuenta pausada se marca en la línea."""
    accounts = [{"username": "u1", "mode": "monitor", "paused": True}]
    text, _, _ = render_list_page(accounts)
    assert "paused" in text.lower() or "pausa" in text.lower()


# --- build_list_keyboard: botones inline (T38, §6.3) ---


def test_build_list_keyboard_callback_data_compacto_t38():
    """T38: callback_data <= 64 bytes con encoding presupuestado."""
    kb = build_list_keyboard(page=1, total_pages=5, now=1_700_000_000)
    for row in kb.inline_keyboard:
        for btn in row:
            assert btn.callback_data is not None
            assert len(btn.callback_data.encode()) <= 64, f"{btn.callback_data} > 64 bytes"


def test_build_list_keyboard_botones_extremos():
    """Primera página: Anterior disabled; última: Siguiente disabled."""
    kb_first = build_list_keyboard(page=0, total_pages=3, now=1_700_000_000)
    first_row = kb_first.inline_keyboard[0]
    assert len(first_row) == 2
    # Anterior disabled, Siguiente enabled
    assert "Anterior" in first_row[0].text
    assert first_row[0].callback_data is None  # disabled
    assert "Siguiente" in first_row[1].text
    assert first_row[1].callback_data is not None

    kb_last = build_list_keyboard(page=2, total_pages=3, now=1_700_000_000)
    last_row = kb_last.inline_keyboard[0]
    assert "Anterior" in last_row[0].text
    assert last_row[0].callback_data is not None
    assert "Siguiente" in last_row[1].text
    assert last_row[1].callback_data is None  # disabled


def test_build_list_keyboard_pagina_unica_sin_botones():
    """Una sola página → sin botones (no hay nada que navegar)."""
    kb = build_list_keyboard(page=0, total_pages=1, now=1_700_000_000)
    assert kb.inline_keyboard == ()


def test_build_list_keyboard_encoding_incluye_pagina_y_ts():
    """Callback data contiene acción, timestamp y página (T38)."""
    kb = build_list_keyboard(page=2, total_pages=5, now=1_700_000_123)
    btn_prev = kb.inline_keyboard[0][0]  # Anterior → página 1
    btn_next = kb.inline_keyboard[0][1]  # Siguiente → página 3
    assert btn_prev.callback_data == "listp:1700000123:1"
    assert btn_next.callback_data == "listp:1700000123:3"


# --- Callback handler: authz (F-18), throttle 2s, expiry 60s, edición ---


def test_parse_list_callback_valido():
    """Callback 'listp:{ts}:{page}' → (page, ts) parseable."""
    page, ts = parse_list_callback("listp:1700000123:2")
    assert page == 2
    assert ts == 1700000123


def test_parse_list_callback_invalido():
    """Callback malformado → None (no revienta)."""
    assert parse_list_callback("basura") is None
    assert parse_list_callback("listp:abc:xyz") is None
    assert parse_list_callback("") is None


@pytest.mark.asyncio
async def test_handle_list_callback_f18_sin_chat():
    """F-18: callback sin effective_chat → rechazado, no revienta."""
    result = await handle_list_callback(
        query=None,
        accounts=[],
        chat_id=None,
        user_id=None,
        settings=None,
        now=1_700_000_000,
    )
    assert result is False


@pytest.mark.asyncio
async def test_handle_list_callback_no_autorizado():
    """§6.3: chat no autorizado → rechazado."""
    from tikdown_rs.core.config import Settings

    settings = Settings(_env_file=None, telegram_chat_id="111")
    result = await handle_list_callback(
        query=None,
        accounts=[],
        chat_id="222",
        user_id=None,
        settings=settings,
        now=1_700_000_000,
    )
    assert result is False


@pytest.mark.asyncio
async def test_handle_list_callback_expirado():
    """§6.3: botón con timestamp > 60s → expirado, no se edita."""
    from tikdown_rs.core.config import Settings

    settings = Settings(_env_file=None, telegram_chat_id="111")
    # timestamp 90s en el pasado
    old_ts = int(time.time()) - 90
    page, ts = parse_list_callback(f"listp:{old_ts}:1")
    assert page == 1
    from tikdown_rs.daemon.telegram.bot import callback_expired

    assert callback_expired(ts, max_age=60) is True
    result = await handle_list_callback(
        query=None,
        callback_data=f"listp:{old_ts}:1",
        accounts=[],
        chat_id="111",
        user_id=None,
        settings=settings,
        now=int(time.time()),
    )
    # expirado → rechazado sin editar
    assert result is False


@pytest.mark.asyncio
async def test_handle_list_callback_throttle_2s():
    """F-18: más de 1 callback en 2s por chat → throttled."""
    from tikdown_rs.core.config import Settings

    settings = Settings(_env_file=None, telegram_chat_id="111")
    accounts = [{"username": f"user{i}", "mode": "history", "paused": False} for i in range(12)]
    shared = {}
    # primer callback OK (ahora)
    ok = await handle_list_callback(
        query=None,
        callback_data="listp:1700000000:0",
        accounts=accounts,
        chat_id="111",
        user_id=None,
        settings=settings,
        now=1_700_000_000,
        last_callback_ts=shared,
    )
    assert ok is True
    # segundo callback inmediato (mismo now, mismo dict compartido) → throttled
    throttled = await handle_list_callback(
        query=None,
        callback_data="listp:1700000000:1",
        accounts=accounts,
        chat_id="111",
        user_id=None,
        settings=settings,
        now=1_700_000_000,
        last_callback_ts=shared,
    )
    assert throttled is False


# --- cmd_list: orquesta /list con teclado (e11s01) ---


@pytest.mark.asyncio
async def test_cmd_list_una_pagina_sin_teclado():
    """/list con <= 5 cuentas → texto sin teclado."""
    from tikdown_rs.daemon.telegram.handlers import cmd_list

    accounts = [{"username": f"user{i}", "mode": "history", "paused": False} for i in range(3)]
    text, kb = await cmd_list(accounts, page=0)
    assert "@user0" in text
    assert kb.inline_keyboard == ()


@pytest.mark.asyncio
async def test_cmd_list_varias_paginas_con_teclado():
    """/list con > 5 cuentas → teclado con botones."""
    from tikdown_rs.daemon.telegram.handlers import cmd_list

    accounts = [{"username": f"user{i}", "mode": "history", "paused": False} for i in range(12)]
    text, kb = await cmd_list(accounts, page=0)
    assert "1/3" in text
    assert len(kb.inline_keyboard) == 1
    assert len(kb.inline_keyboard[0]) == 2


@pytest.mark.asyncio
async def test_cmd_list_escape_html():
    """/list escapa usernames (T40/F-05)."""
    from tikdown_rs.daemon.telegram.handlers import cmd_list

    accounts = [{"username": "a<b", "mode": "history", "paused": False}]
    text, _ = await cmd_list(accounts, page=0)
    assert "a&lt;b" in text

# Story e11s01: Paginación /list con botones inline (T38, §6.3, F-18)

## 1. Metadata

| Campo | Valor |
|-------|-------|
| story_id | e11s01 |
| epic | e11-paginacion |
| type | feat |
| risk | P1 |
| context | domain |
| bcps | 5 |
| delta | ADDED |

## 2. Título

Paginación del comando /list del bot con botones inline (◀️ Anterior / Siguiente ▶️).

## 3. Problema

`/list` actualmente muestra resultados limitados sin paginación. §6.4 explícitamente deja la
paginación real (botones con offset para listas largas) en el backlog; este item la implementa.

## 4. Contexto

El bot (`daemon/telegram/`) tiene helpers T38/F-18 ya implementados (`build_callback_data`,
`callback_expired`, `is_authorized`) pero **no registra handlers de comandos ni callbacks** en
`start()`. El comando `/list` en `handlers.dispatch()` es un stub que no llama a services.
`services/accounts.list_accounts()` ya existe (ordena por username).

## 5. Alcance

- Lógica pura de paginación (`render_list_page`) testeable.
- Botones inline ◀️/▶️ con callback_data compacto (T38) y expiración real 60s (§6.3).
- Registro de `CallbackQueryHandler` en el bot.
- Throttle de 2s por chat también en callbacks (F-18).
- Authz doble en callbacks, tolerante a updates sin chat (F-18).

## 6. Fuera de alcance

- Implementación completa de TODOS los comandos (los demás siguen como stubs).
- Supervisión del polling (item 3 del backlog).
- Persistencia del offset en DB (el throttle en memoria es suficiente).
- Multi-usuario complejo (solo chat autorizado).

## 7. Stack y dependencias

- `python-telegram-bot` (ya instalado): `InlineKeyboardMarkup`, `CallbackQueryHandler`.
- `services/accounts.list_accounts` (ya existe).
- **Sin dependencias nuevas.**

## 8. Diseño

```
/list → render_list_page(accounts, 0) + build_list_keyboard(0, total)
  ↓ (callback "listp:{ts}:{page}")
CallbackQueryHandler:
  authz (chat+user, F-18) → throttle 2s → answer() → expiry 60s → parse page →
  render_list_page(page) + edit_message_text(nuevo texto, nuevo teclado)
```

`callback_data` = `listp:{epoch}:{page}` — acción corta + timestamp + página, ≤ 64 bytes (T38).

## 9. Requisitos

### ADDED: Paginación de /list
**After:** `/list` pagina cuentas (page_size 5) con botones inline ◀️ Anterior / Siguiente ▶️;
cada página muestra @username (mode, paused), escape HTML.

### ADDED: callback_data compacto (T38)
**After:** `listp:{ts}:{page}` ≤ 64 bytes; timestamp embebido para expiración real de 60s (§6.3).

### ADDED: Authz + throttle en callbacks (F-18)
**After:** Los callbacks pasan la doble autorización (chat + from_user.id), throttle de 2s por
chat, y toleran updates sin `effective_chat` (None) sin reventar.

## 10. Comportamiento

1. `/list` con ≤ 5 cuentas → una página sin botones.
2. `/list` con > 5 → página 1 + botones Anterior (disabled) / Siguiente.
3. Pulsar Siguiente → edita el mensaje con la página 2, botones actualizados.
4. Botón expirado (> 60s) → mensaje "Botón expirado", sin editar.
5. Callback sin autorización → rechazado, `query.answer()` con aviso.
6. Más de 1 callback/2s en el mismo chat → segundo rechazado (throttle).

## 11. Pasos de implementación

1. `render_list_page(accounts, page, page_size=5)` — lógica pura → verify: `uv run pytest tests/telegram/test_list_pagination.py -q`
2. `build_list_keyboard(page, total_pages)` — botones inline T38 → verify: `uv run pytest tests/telegram/test_list_pagination.py -q`
3. Callback handler: authz + throttle + answer + expiry + edit → verify: `uv run pytest tests/telegram/test_list_pagination.py -q`
4. Throttle 2s por chat en callbacks (F-18) → verify: `uv run pytest tests/telegram/test_list_pagination.py -q`
5. Tests F.I.R.S.T. + integración services → verify: `uv run pytest tests/telegram/ -q`

## 12. Script de verificación (step-by-step)

1. `uv run pytest tests/telegram/ -q` → todos los tests del bot pasan.
2. `uv run pytest --cov=tikdown_rs --cov-report=term -q` → 192+ tests, sin regresión.
3. `uv run ruff check . && uv run ruff format --check .` → lint OK.
4. `uv run python -c "from tikdown_rs.daemon.telegram.handlers import render_list_page; ..."` → la lógica pura responde.
5. `uv run tikdown-rs --version` → CLI intacta (smoke).

## 13. Criterios de aceptación

- [ ] `render_list_page` pagina correctamente (clamp, total_pages, vacío, escape).
- [ ] `build_list_keyboard` genera botones ◀️/▶️ con callback_data ≤ 64 bytes (T38).
- [ ] Callback handler autentica (F-18), throttle 2s, expiry 60s, edita la página.
- [ ] Botones deshabilitados en extremos.
- [ ] Sin dependencias nuevas.

## 14. Definición de éxito

`tests/telegram/` pasa, sin regresión en la suite completa, lint verde, y la lógica de
paginación verificable por test.

## 15. Saliendo

- Rama `feat/e11-paginacion-list` vía kickoff-branch.
- Commits Conventional Commits separados RED/GREEN.

## 16. Riesgos

| Riesgo | Detección |
|--------|-----------|
| callback_data > 64 bytes | Test T38 con el callback más largo |
| Expiración mal validada | Test 60s (futuro/pasado) |
| Throttle roto en ráfaga | Test 2s por chat |
| Update sin chat revienta | Test F-18 con chat=None |

## 17. Criterios de aceptación (checklist)

- [ ] Story spec en `specs/epics/e11-paginacion/e11s01-paginacion-list.md`
- [ ] Tasks con `status: failing` (no pre-marcados)
- [ ] Tests en `tests/telegram/test_list_pagination.py`

## 18. Seguimiento

- Estado: `failing` → `passing` tras verify-work.
- `state.yaml` `active_flow` → `build_epic`.

## 19. Notas

- T38: callback_data ≤ 64 bytes, encoding compacto `listp:{ts}:{page}`.
- §6.3: botones con expiración real (timestamp validado, 60s).
- F-18: guard tolera updates sin effective_chat (None).
- Throttle 1 comando/2s por chat aplica también a callbacks.

## 20. Riesgo (técnico)

P1 — lógica de presentación + callback handling; sin datos sensibles nuevos, sin red TikTok.

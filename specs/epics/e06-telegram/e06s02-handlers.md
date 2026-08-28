# Story e06s02 — Handlers + notificaciones (comandos planos + spool)

**type:** feat
**risk:** P0
**context:** domain
**BCPs:** 10
**status:** planned

## 1. Business narrative

El bot ejecuta comandos planos con paridad funcional con la CLI (§6.4) y envía notificaciones exhaustivas (§0.9) con spool ante fallos de red (T42), coalescing en ráfagas (L-I3), clip() a 4096 (F-07) y escape HTML (T40).

## 2. Actors

- **Usuario** — comandos `/stats /disk /list /last` etc.
- **Daemon** — emite eventos de notificación.
- **Spool** — persiste eventos ante fallos de red.

## 3. Problem statement

Los handlers deben orquestar `services/*` (paridad funcional, §6.4), y el servicio de notificaciones debe entregar sin pérdidas: spool (T42/F-06), coalescing (L-I3), clip 4096 (F-07/T39), escape HTML (T40/L-H7), paridad plantilla↔productor (T34/F-08).

## 4. Requirements

#### ADDED: Handlers de comandos planos (§6.4)
**After:** `daemon/telegram/handlers.py` con `/stats /disk /list /last /backfill /cookies /check /add /pause /resume /notify /monitor` — paridad FUNCIONAL con la CLI (misma función de services/* detrás). Salida sin markup rico (L-A6). Escape HTML en todo contenido dinámico (`_esc()`, T40/F-05).

#### ADDED: Catálogo de eventos con paridad (T34/F-08)
**After:** `core/notifications/events.py` — catálogo + plantillas; test de paridad plantilla↔productor (T34, scan excluyendo events.py, F-08). El render no duplica el `@` (L-H7).

#### ADDED: Servicio de envío con spool (T42/F-06/L-I1)
**After:** `core/notifications/telegram.py` — `ExtBot` + rate limiter; `send_event()` con captura amplia `except Exception` (L-I1); ante fallo de red → spool `pending_notifications` (**solo con ENABLE_EXTERNAL_NOTIFICATIONS=true**, T42); spool guarda el evento ORIGINAL (event, payload), no el render (F-06); drenado en transición a online y en arranque.

#### ADDED: clip() y coalescing (F-07/T39/L-I3)
**After:** `clip(text, limit=4096)` compartido con el sufijo DENTRO del límite (F-07); mensajes >4096 truncados con indicación (T39); errores BadRequest descartados sin re-spolear (T39). Coalescing: umbral `>=` con bandera consumible (L-I3).

#### ADDED: parse_mode=HTML + degradación (T40)
**After:** `parse_mode=HTML` + `html.escape()` en todo contenido dinámico; degradación a texto plano ante `can't parse entities` (BadRequest). MarkdownV2 prohibido.

#### ADDED: Propagación del canal (L-I5)
**After:** Todo job que lanza corrutina con canal de eventos lo PROPAGA explícitamente (`on_event=on_event`).

## 5. Solution and main flow

1. `core/notifications/events.py`: catálogo + plantillas.
2. `core/notifications/telegram.py`: ExtBot + spool + clip + coalescing.
3. `daemon/telegram/handlers.py`: comandos planos.

## 6. Alternative flows / edge cases

- **Red caída**: spool (T42).
- **Ráfaga**: coalescing (L-I3).
- **Mensaje largo**: clip (F-07).
- **Entities rotas**: degradación texto plano (T40).

## 7. Assumptions

- Bot dispatcher (e06s01), ExtBot en telegram.ext.

## 8. Constraints

- Spool solo con notificaciones habilitadas (T42).
- clip con sufijo dentro (F-07).
- HTML escape (T40); MarkdownV2 prohibido.
- Paridad plantilla↔productor (T34).

## 9. Dependencies

- e06s01 (bot), e03-e05 (services).

## 10. Interfaces

- `core/notifications/events.py` → catálogo.
- `core/notifications/telegram.py` → send_event, clip.
- `daemon/telegram/handlers.py` → comandos.

## 11. Test plan

- `tests/telegram/test_handlers.py`: comandos, escape (T40), L-H7.
- `tests/telegram/test_notifications.py`: spool (T42/F-06), clip (F-07), coalescing (L-I3), paridad (T34).

## 12. Data

- `pending_notifications`.

## 13. Security considerations

- Escape HTML (XSS vía contenido TikTok).

## 14. Performance

- Coalescing reduce mensajes; clip evita Message_too_long.

## 15. Operational concerns

- Spool drenado en online/arranque.

## 16. Risks

- **Paridad rota**: T34 (test).

## 17. Acceptance criteria

- [ ] Handlers de comandos planos (§6.4) con paridad funcional.
- [ ] Catálogo + paridad plantilla↔productor (T34/F-08).
- [ ] Spool solo con notificaciones habilitadas (T42); guarda evento original (F-06).
- [ ] clip() 4096 con sufijo dentro (F-07/T39).
- [ ] HTML escape + degradación (T40); L-H7 (no doble @).
- [ ] Coalescing >= umbral consumible (L-I3).
- [ ] Tests en `tests/telegram/` pasan.

## 18. Out of scope

- Backlog: paginación de /list, supervisión polling.

## 19. Risks (detailed)

- **T34**: paridad plantilla↔productor con test.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/telegram/` pasa.
- Tasks `status: passing` en `e06s02-tasks.yaml`.

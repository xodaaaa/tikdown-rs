# Story e06s01 — Dispatcher PTB async (mismo event loop) + rate-limiter

**type:** feat
**risk:** P0
**context:** domain
**BCPs:** 6
**status:** planned

## 1. Business narrative

El bot de Telegram (control remoto) corre en el **mismo event loop** del daemon con el ciclo manual de PTB (T10). Recibe dependencias **inyectadas** (T26), aplica doble autorización (chat + user, §6.3), rate limiting (T41), callback_data compacto (T38) y handlers idempotentes (T48).

## 2. Actors

- **Usuario** — controla el daemon vía Telegram.
- **Daemon** — inyecta dependencias y arranca el bot.
- **Telegram Bot API** — long polling.

## 3. Problem statement

El bot debe integrarse sin romper el event loop (T10), sin fugas de engines (T26), con autorización estricta (chat + user, §6.3), sin 429 (T41) y con callback_data ≤ 64 bytes (T38).

## 4. Requirements

#### ADDED: Ciclo de vida manual (T10)
**After:** `daemon/telegram/bot.py` usa `initialize() → start() → updater.start_polling(timeout=25)` — **nunca `run_polling()`** (T10, RuntimeError en loop existente). Apagado: `updater.stop() → stop() → shutdown()`.

#### ADDED: Dependencias inyectadas (T26)
**After:** El bot recibe engine, motor de descarga, archive y clave Fernet **inyectados por el daemon en el constructor** (T26) — nunca creados por comando. Flag `owns_engine` decide si dispone el engine al terminar.

#### ADDED: Doble autorización (§6.3, F-18)
**After:** Solo `TELEGRAM_CHAT_ID` ejecuta comandos; además se verifica `from_user.id` (configurable vía `TELEGRAM_USER_ID`, lista; vacío = propietario del chat). Guard tolera updates sin `effective_chat` (F-18). Otro chat → `bot.unauthorized_attempt`.

#### ADDED: Rate limiting (T41)
**After:** `Application.builder().rate_limiter(AIORateLimiter(max_retries=3))` — el extra `[rate-limiter]` es obligatorio (L-H2). El servicio de notificaciones usa `ExtBot`.

#### ADDED: callback_data compacto (T38)
**After:** `callback_data` ≤ 64 bytes: encoding compacto presupuestado (acción corta + timestamp epoch + payload acotado). Botones con expiración real (timestamp embebido, validado en handler, 60s).

#### ADDED: Handlers idempotentes (T48)
**After:** Todos los handlers idempotentes ante re-entrega de updates (PTB mantiene offset en memoria; Telegram puede re-entregar tras reinicio).

#### ADDED: Nunca getUpdates manual (T71)
**After:** Verificar el bot con `getMe`/`sendMessage`, nunca `getUpdates` manual (mata el polling con Conflict 409).

## 5. Solution and main flow

1. `daemon/telegram/bot.py`: TelegramBot con ciclo manual (T10), deps inyectadas (T26).
2. Auth (doble chat+user, §6.3).
3. Rate limiter (T41) + ExtBot.

## 6. Alternative flows / edge cases

- **Update sin chat**: tolerar (F-18).
- **Botón expirado**: callback_data con timestamp validado (60s).

## 7. Assumptions

- PTB 22.8 + aiolimiter instalados (e01s01).

## 8. Constraints

- Nunca run_polling (T10).
- Deps inyectadas, no creadas por comando (T26).
- callback_data ≤ 64 bytes (T38).
- Rate limiter obligatorio (T41).

## 9. Dependencies

- e02s03 (runner arranca bot), e01s02 (Settings), e04 (motor).

## 10. Interfaces

- `daemon/telegram/bot.py` → TelegramBot.
- Consumido por daemon runner (e02s03), handlers (e06s02).

## 11. Test plan

- `tests/telegram/test_bot_lifecycle.py`: T10 (ciclo manual, no run_polling).
- `tests/telegram/test_bot_authz.py`: §6.3 doble auth, F-18.
- `tests/telegram/test_bot_limits.py`: T38 (callback ≤ 64), T41 (rate limiter).

## 12. Data

- Ninguno directo (usa services).

## 13. Security considerations

- Doble auth (chat + user); unauthorized_attempt auditado.

## 14. Performance

- Rate limiter evita 429.

## 15. Operational concerns

- T71: verificar con getMe/sendMessage.

## 16. Risks

- **run_polling accidental**: T10 (test).

## 17. Acceptance criteria

- [ ] Ciclo manual T10 (initialize/start/start_polling; nunca run_polling).
- [ ] Deps inyectadas T26 + owns_engine.
- [ ] Doble auth §6.3 (chat + user) + F-18.
- [ ] Rate limiter T41 (AIORateLimiter + ExtBot).
- [ ] callback_data ≤ 64 (T38).
- [ ] Handlers idempotentes (T48).
- [ ] Tests en `tests/telegram/` pasan.

## 18. Out of scope

- Handlers de comandos (e06s02).
- Notificaciones/spool (e06s02).

## 19. Risks (detailed)

- **run_polling en loop**: T10.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/telegram/` pasa.
- Tasks `status: passing` en `e06s01-tasks.yaml`.

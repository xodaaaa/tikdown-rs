# m5-bot-telegram — Bot de Telegram (§6, hito M5)

Source of truth: `specs/Plan de implementacion tikdown-rs.md` §6.1-§6.5, §13.2 (T-BOT-1..18),
§12.1 (criterio de aceptación M5: authz en comandos y callbacks; recuperación de polling verificada
empíricamente). Base: master `1c3d342` (post-merge review-bloqueantes). Cada fix/unidad = un commit
revertible, tests primero (§16.10). Regla de dependencias: `services/*` no importa `cli/`, `daemon/`
ni `yt_dlp`; el bot no importa `cli/` (test de arquitectura).

## Tasks

- [x] T1 — Prerrequisito M5 (hallazgo M5 de review-hallazgos): extra `[rate-limiter]` en
      `python-telegram-bot` (T-BOT-8). Test primero: `AIORateLimiter(max_retries=3)` construible.
      Commit `76cf34f` (gate: 531 passed, 1 deselected live).
- [x] T2 — Contrato de notificaciones §6.2: PREEXISTENTE del hito M4 (`core/notifications/events.py`
      + `service.py` con `NoopNotificationService`). Test de paridad ya en verde
      (`tests/test_notifications.py`, 13 tests). Sin trabajo nuevo en M5.
- [x] T3 — Seguridad §6.3: `src/tikdown_rs/bot/security.py` (delegado a writer, tests-first).
      Commit `98b50e0` (22 tests nuevos; suite 553 passed, ruff clean). Desviaciones registradas:
      `throttle()` público (guard de 1 chat), `CLIP_SUFFIX` público, throttle registra solo llamadas
      aceptadas, allowlist vacía ≡ no configurada.
- [x] T4 — Paginación §6.4: `src/tikdown_rs/bot/pagination.py` (delegado a writer, tests-first).
      Commit `2804775` (27 tests nuevos; suite 580 passed, ruff clean). Campos reales del modelo:
      `username`/`backfill_status`/`video_count` (no handle/state). Desviación aceptada: expiración
      estricta `now - ts > 60` (tolera clock skew); página vacía → total_pages 0.
- [ ] T5 — Dispatcher de comandos §6.2: `/start /help /list /add /stats /disk /status /monitor /check
      /backfill /pause /resume /remove /cookies /notify /last`; paridad **funcional** con
      `services/*` (el dispatcher solo orquesta); handlers idempotentes (T-BOT-4); upload de cookies
      con límite 10 MB (metadato + tamaño real), `mkstemp` + `os.close(fd)` + borrado en `finally`
      (T-COOKIES-7).
- [ ] T6 — Integración en el daemon §6.1/§5.1(paso 8): `initialize() → start() →
      updater.start_polling(timeout=25)`; NUNCA `run_polling()`; apagado `updater.stop() → stop() →
      shutdown()` (T-BOT-1); engine único inyectado + `owns_engine` (T-BOT-3); verificación con
      `getMe`/`sendMessage`, nunca `getUpdates` manual (T-BOT-2); supervisión activa.
- [ ] T7 — Supervisión del polling §6.5: tarea supervisada con healthcheck `getMe` cada
      `POLLING_HEALTHCHECK_INTERVAL` (30 s); tras 3 fallos consecutivos reinicio completo
      (`stop()` + `start()`) sin reiniciar el daemon; flag `_restarting` anticoncurrencia.
      Verificación empírica del polling (aviso §6.5, PTB ≥20): pendiente del usuario/entorno.
- [ ] T8 — Gate M5: pytest en verde + test de arquitectura (bot no importa `cli/`) + ruff.

## Notas

- §0.4: sin tablas/settings nuevas más allá de las que §6 y §11 ya definen
  (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_USER_ID`,
  `POLLING_HEALTHCHECK_INTERVAL`, `POLLING_HEALTHCHECK_MAX_FAILURES`).
- Envío push de notificaciones FUERA del alcance base [F] §17.1: solo el contrato (T2).
- Verificación empírica de recuperación de polling (criterio de aceptación M5) requiere entorno
  real con token: pedir al usuario cuando llegue el momento.

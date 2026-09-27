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
- [~] T5 — Dispatcher de comandos §6.2:
      - [x] T5a — Core + lectura (`/start /help /list /stats /disk /status /last` + callback
            `listp:`): commit `0459ef6` (38 tests nuevos; suite 618 passed estable ×2, ruff clean).
            `/stats` y `/last` responden "pending (M6)" (el CLI también es stub — paridad funcional
            OK). PTB API verificada en runtime (22.8). Desviación: `TELEGRAM_CHAT_ID` no parseable →
            sentinel `-1` (deny-all, default seguro) — confirmar con el usuario.
      - [x] T5b — Mutadores + upload: commit `7c74ea4` (35 tests nuevos; suite 653 passed ×2,
            ruff clean). `/monitor` y `/check` responden pending: el CLI es stub explícito
            (accounts.py: "check needs the engine listing round") y no existe verbo `monitor` en
            el CLI — paridad funcional OK. Upload: rechazo por metadato ANTES de descargar,
            tamaño real post-descarga, mkstemp + fd close inmediato + unlink en finally
            (idempotente), authz/throttle aplican a uploads, deleteMessage best-effort.
- [x] T6 — Integración daemon §6.1/§5.1(paso 8): commit `d648475`. Bot en el punto marcado
      (run.py ~636-643), solo si `telegram_bot_token` está seteado; session_factory del daemon
      inyectada (UN engine, T-BOT-3); `owns_engine` NO añadido (flag con un solo valor posible =
      flexibilidad muerta — el daemon dispone su engine en shutdown); lifecycle estricto T-BOT-1;
      tarea supervisada `bot-polling-supervision`; degradación graciosa si el bot falla al arrancar
      (el daemon sigue funcional); sin `getUpdates` manual (grep-verified).
- [x] T7 — Supervisión del polling §6.5: commit `a20d838`. `bot/supervision.py`: healthcheck
      `get_me` cada `polling_healthcheck_interval` con `asyncio.wait_for(min(interval, 10 s))`;
      3 fallos consecutivos → reinicio completo sin reiniciar el daemon; `_restarting` anticoncurrencia;
      restart fallido → reintento próximo ciclo (contador NO se resetea); 12 tests deterministas.
      **Verificación empírica (criterio de aceptación M5) PENDIENTE**: matar polling y observar
      recuperación requiere entorno real con token — pedir al usuario.
- [x] T8 — Gate M5: pytest `669 passed, 1 deselected (live)` (estable, ×2 corridas por unidad);
      ruff check + format clean; test de arquitectura verde (bot no importa `cli/`,
      `tests/test_architecture.py:4`). Suite total M5: 580 → 669 (+89 tests).

## Notas

- §0.4: sin tablas/settings nuevas más allá de las que §6 y §11 ya definen
  (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_USER_ID`,
  `POLLING_HEALTHCHECK_INTERVAL`, `POLLING_HEALTHCHECK_MAX_FAILURES`).
- Envío push de notificaciones FUERA del alcance base [F] §17.1: solo el contrato (T2).
- Verificación empírica de recuperación de polling (criterio de aceptación M5) requiere entorno
  real con token: pedir al usuario cuando llegue el momento.

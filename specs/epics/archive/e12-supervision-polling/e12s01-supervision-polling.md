# Story e12s01: Healthcheck periódico del bot (getMe) + reinicio automático (T71, §6.1)

## 1. Metadata

| Campo | Valor |
|-------|-------|
| story_id | e12s01 |
| epic | e12-supervision-polling |
| type | feat |
| risk | P1 |
| context | domain |
| bcps | 5 |
| delta | ADDED |

## 2. Título

Supervisión del polling de Telegram: healthcheck periódico (getMe) + reconexión automática sin reiniciar el daemon.

## 3. Problema

T71: una sesión de `getUpdates` manual contra un bot en polling → `Conflict` (409) → el polling muere
y el daemon NO lo detecta (sigue healthy con el bot muerto en silencio). La recuperación actual es
reiniciar el contenedor. §6.1/§18 backlog: la supervisión debe detectar la muerte del bot y
reiniciarlo automáticamente **sin reiniciar el daemon**.

## 4. Contexto

`TelegramBot` (bot.py) tiene el ciclo de vida manual (T10) pero **no supervisa el polling**.
`run.py` `_start_bot` crea su propio `Application` sin handlers ni supervisión. El daemon ya tiene
`create_supervised_task` (T27/T28) para tareas de fondo. PTB #3430: en PTB ≥20 los errores de
`get_updates` pueden NO llegar a `add_error_handler` — la vía de detección debe ser empírica/robusta:
un healthcheck periódico con `getMe` (nunca `getUpdates`, T71).

## 5. Alcance

- Tarea de supervisión periódica que llama `get_me()` (getMe).
- Tras N fallos consecutivos → reinicio del bot (stop + start) sin tocar el daemon.
- Flag anti-reinicio-concurrente; reintento con backoff.
- Cableado: `TelegramBot.start()` lanza la supervisión; `run.py` usa `TelegramBot`.
- Sin depender de `add_error_handler` (poco fiable en PTB ≥20, #3430).

## 6. Fuera de alcance

- Persistencia del offset de `getUpdates` en DB (T48 — backlog, no aquí).
- Reinicio de TODO el daemon (solo el bot).
- Múltiples bots.
- Notificaciones push del evento de reinicio (solo log).

## 7. Stack y dependencias

- `python-telegram-bot` (ya instalado): `bot.get_me()`.
- `core/tasks.create_supervised_task` (ya existe, T27/T28).
- **Sin dependencias nuevas.**

## 8. Diseño

```
TelegramBot.start()
  └─ arranca polling (T10)
  └─ lanza create_supervised_task(_supervise_polling(), "bot-supervision")  (T27)
        loop cada POLLING_HEALTHCHECK_INTERVAL (30s):
          try: await self._app.bot.get_me() → ok, reset fallos
          except: fallos += 1; si >= N (3) → reiniciar:
              stop() (updater.stop → stop → shutdown)
              start() (re-arranca polling)
              log bot.restarted
        stop() cancela la tarea de supervisión (T28)
```

Detección robusta: `getMe` es la vía empírica (T71/§6.1), no `add_error_handler` (#3430).

## 9. Requisitos

### ADDED: Healthcheck periódico (getMe)
**After:** `TelegramBot` ejecuta una tarea supervisada que llama `get_me()` cada
`POLLING_HEALTHCHECK_INTERVAL` segundos (default 30s). Usa `getMe`, nunca `getUpdates` (T71).

### ADDED: Reinicio automático sin reiniciar el daemon
**After:** Tras N fallos consecutivos de `get_me()` (default 3), el bot se reinicia (stop + start)
en el mismo event loop, sin afectar al resto del daemon. Flag `_restarting` evita reinicios
concurrentes; reintento con backoff si el reinicio falla.

### ADDED: Cableado en el daemon
**After:** `TelegramBot.start()` lanza la supervisión (T27); `run.py` usa `TelegramBot` con
supervisión. La tarea se cancela en `stop()` (T28).

## 10. Comportamiento

1. Bot sano → `getMe` exitoso → sin acción.
2. `getMe` falla (409 Conflict, red, bot muerto) → contador de fallos.
3. Tras 3 fallos → stop + start del bot → log `bot.restarted`.
4. Si el reinicio falla → reintento en el siguiente ciclo (backoff).
5. El supervisor corre como tarea (no bloquea el loop); se cancela en stop().

## 11. Pasos de implementación

1. `_supervise_polling()` — healthcheck getMe periódico → verify: `uv run pytest tests/telegram/test_bot_supervision.py -q`
2. Reinicio automático (stop + start, flag `_restarting`, backoff) → verify: `uv run pytest tests/telegram/test_bot_supervision.py -q`
3. Cablear: start() lanza supervisión; run.py usa TelegramBot → verify: `uv run pytest tests/telegram/test_bot_supervision.py -q`
4. Tests F.I.R.S.T. → verify: `uv run pytest tests/telegram/ -q`

## 12. Script de verificación (step-by-step)

1. `uv run pytest tests/telegram/test_bot_supervision.py -q` → tests de supervisión pasan.
2. `uv run pytest tests/telegram/ -q` → todos los tests del bot pasan.
3. `uv run pytest --cov=tikdown_rs --cov-report=term -q` → suite completa sin regresión.
4. `uv run ruff check . && uv run ruff format --check .` → lint OK.
5. `uv run tikdown-rs --version` → CLI intacta (smoke).

## 13. Criterios de aceptación

- [ ] `_supervise_polling` llama `get_me()` periódicamente (nunca `getUpdates`, T71).
- [ ] Tras N fallos → reinicio del bot (stop + start) sin tocar el daemon.
- [ ] Flag `_restarting` evita reinicios concurrentes.
- [ ] La supervisión corre como tarea supervisada (T27) y se cancela en stop() (T28).
- [ ] Sin dependencias nuevas.

## 14. Definición de éxito

`tests/telegram/` pasa, la supervisión detecta un `getMe` fallido y reinicia, sin regresión en la
suite completa, lint verde.

## 15. Saliendo

- Rama `feat/e12-supervision-polling` vía kickoff-branch.
- Commits Conventional Commits separados RED/GREEN.

## 16. Riesgos

| Riesgo | Detección |
|--------|-----------|
| El reinicio concurrente corrompe el estado | Test con `_restarting=True` |
| El supervisor bloquea el event loop | Test de que corre como tarea |
| `add_error_handler` no captura el 409 (PTB #3430) | No se depende de él; getMe es la vía |
| Backoff roto | Test de reintento tras fallo de reinicio |

## 17. Criterios de aceptación (checklist)

- [ ] Story spec en `specs/epics/e12-supervision-polling/e12s01-supervision-polling.md`
- [ ] Tasks con `status: failing` (no pre-marcados)
- [ ] Tests en `tests/telegram/test_bot_supervision.py`

## 18. Seguimiento

- Estado: `failing` → `passing` tras verify-work.
- `state.yaml` `active_flow` → `build_epic`.

## 19. Notas

- T71: verificar el bot SIEMPRE con `getMe`/`sendMessage`, nunca `getUpdates`.
- §6.1: la supervisión del polling es backlog del MVP, ahora implementado.
- PTB #3430: en PTB ≥20 la captura del `Conflict` vía `add_error_handler` es poco fiable — la vía
  de detección es el healthcheck getMe, verificado empíricamente por test.

## 20. Riesgo (técnico)

P1 — lógica de supervisión + reconexión; sin datos sensibles nuevos, sin red TikTok. El reinicio
del bot no afecta al daemon (solo el bot).

# Story e02s03 — Runner del daemon (event loop único, scheduler, drenaje)

**type:** feat
**risk:** P0
**context:** infra
**BCPs:** 8
**status:** planned

## 1. Business narrative

El daemon es el proceso de larga duración (entrypoint de Docker). Necesita un runner que: ejecute TODO el ciclo (start + run + shutdown) en **UN único `asyncio.run(_lifecycle())`** (L-B1, crítico), arranque con fail-fast (T25: `validate_for_daemon`; T6: selfcheck), migre con reaplicación de logging (T72, L-B3), lance jobs de APScheduler como **tareas supervisadas** (T27/T28), drene el registro en el apagado (T9), y arranque el monitor **siempre detenido** (T5.1).

## 2. Actors

- **Usuario** — `tikdown-rs daemon run` (foreground), `daemon stop`.
- **Docker** — entrypoint del daemon.
- **Scheduler APScheduler** — jobs de intervalo.
- **Bot de Telegram** — mismo event loop (T10).

## 3. Problem statement

Sin un ciclo de vida en un solo loop, `daemon stop` deja el proceso zombi (L-B1). Sin fail-fast, una config inválida deja el daemon a medias (T25). Sin reaplicar logging tras migrar, docker logs queda con 0 bytes (T72). El drenaje real lo hace el registro de tareas, no el scheduler (T9/T27/T28).

## 4. Requirements

#### ADDED: Ciclo de vida en un único asyncio.run (L-B1)
**After:** `daemon/run.py` define `_lifecycle()`: start + run + shutdown corren dentro de **UN solo `asyncio.run(_lifecycle())`** — nunca un `asyncio.run()` por fase (cada llamada crea un loop nuevo y el scheduler queda atado al loop viejo).

#### ADDED: Arranque fail-fast (§5.1)
**After:** Orden del arranque: (1) `settings.validate_for_daemon()` (T25); (2) selfcheck completo de impersonación (T6) → aborta si falla; (3) migraciones idempotentes con `asyncio.to_thread(apply_migrations)` (L-B3, T29/T68); (4) **reaplicar logging tras migrar** (`setup_logging(force=True)`, T72).

#### ADDED: Jobs como tareas supervisadas + monitor detenido (T27/T28/T9/T5.1)
**After:** Los jobs de APScheduler lanzan su trabajo como `create_supervised_task()` (T27). El apagado **drena el registro de tareas** (`cancel_pending_tasks`), no el scheduler (T9/T28). El monitor **siempre arranca detenido** (`MONITOR_AUTOSTART=false`, T5.1).

#### ADDED: Bot en el mismo loop (T10)
**After:** Si el bot está habilitado, usa el ciclo manual de PTB: `initialize() → start() → updater.start_polling(timeout=25)` — **nunca `run_polling()`** (T10). Apagado: `updater.stop() → stop() → shutdown()`.

#### ADDED: Helpers mutadores commitean internamente (T37/T17)
**After:** `core/daemon_state.py` expone helpers mutadores (p. ej. `set_stop_requested`) que **commitean internamente** (T37) y usan singleton con `INSERT ... ON CONFLICT DO NOTHING` + commit inmediato (T17).

## 5. Solution and main flow

1. `core/daemon_state.py`: helpers mutadores con commit interno (T37/T17).
2. `daemon/run.py`: `_lifecycle()` único (L-B1), arranque fail-fast (T25/T6), migraciones + re-logging (T72/L-B3), scheduler con jobs supervisados (T27/T28), monitor detenido (T5.1), bot manual (T10).
3. `daemon/jobs.py`: jobs de intervalo (monitor, cookies, selfcheck, probe, heartbeat) como tareas supervisadas.

## 6. Alternative flows / edge cases

- **SIGTERM/SIGINT**: watcher de `stop_requested` detecta y completa el apagado en el mismo loop (L-B1).
- **Config inválida**: validate_for_daemon aborta (T25).
- **Impersonación rota**: selfcheck aborta (T6).

## 7. Assumptions

- APScheduler 3.11.3, PTB 22.8 instalados.
- `MONITOR_AUTOSTART` default false.

## 8. Constraints

- Un solo `asyncio.run(_lifecycle())` (L-B1).
- Jobs como tareas supervisadas (T27).
- Drenaje por registro, no scheduler (T9/T28).
- Bot con ciclo manual (T10).
- Helpers con commit interno (T37).

## 9. Dependencies

- e01s02 (Settings), e01s03 (logging), e01s04 (models/migraciones), e02s01 (tasks), e02s02 (selfcheck/crypto).

## 10. Interfaces

- `daemon/run.py` → `_lifecycle()`, `run_daemon()`.
- `daemon/jobs.py` → jobs supervisados.
- Consumido por CLI `daemon run` (e08) y Docker.

## 11. Test plan

- `tests/daemon/test_runner.py`: arranque fail-fast, migraciones + re-logging (T72), monitor detenido (T5.1).
- `tests/daemon/test_runner_shutdown.py`: L-B1 (watcher detecta stop_requested), drenaje (T9/T28), bot manual (T10).

## 12. Data

- `daemon_state` (singleton, helpers T37).

## 13. Security considerations

- Fail-fast (T25/T6) evita arranques a medias.
- Monitor detenido por defecto (T5.1) — seguridad.

## 14. Performance

- Un loop; sin hilos extra (excepto to_thread para migraciones).

## 15. Operational concerns

- Foreground (no fork); Docker lo gestiona.

## 16. Risks

- **Zombi por loop múltiple**: L-B1 (test de regresión del watcher).
- **docker logs 0 bytes**: T72 (reaplicar logging).

## 17. Acceptance criteria

- [ ] `_lifecycle()` en un solo `asyncio.run` (L-B1).
- [ ] Arranque: validate_for_daemon (T25) → selfcheck (T6) → migraciones + re-logging (T72/L-B3).
- [ ] Jobs como tareas supervisadas (T27); drenaje por registro (T9/T28).
- [ ] Monitor arranca detenido (T5.1).
- [ ] Bot manual `initialize → start → start_polling` (T10); apagado `updater.stop → stop → shutdown`.
- [ ] Helpers mutadores commitean internamente (T37/T17).
- [ ] Tests en `tests/daemon/test_runner*.py` pasan.

## 18. Out of scope

- Heartbeat persistido (e02s04).
- Comandos CLI `daemon status/stop/healthcheck` (e02s04).
- Contenido real de jobs (monitor, cookies) — e03+.

## 19. Risks (detailed)

- **Zombi**: L-B1 — un solo loop con watcher de stop_requested.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/daemon/test_runner.py tests/daemon/test_runner_shutdown.py` pasa.
- Tasks `status: passing` en `e02s03-tasks.yaml`.

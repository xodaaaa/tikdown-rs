# Story e07s04 — Contención SQLite (db_busy_count_5min + alerta)

**type:** feat
**risk:** P0
**context:** infra
**BCPs:** 3
**status:** planned

## 1. Business narrative

El listener `handle_error` del engine captura `database is locked` (ya en e01s04), el contador con ventana rotativa de 5 min ya existe (e02s04). Esta story añade: **persistir el contador en el heartbeat** y la **alerta `daemon.db_contention`** con dedupe por flanco cuando supera `DB_BUSY_TIMEOUT_ALERT_THRESHOLD` (default 20) (§5.8).

## 2. Actors

- **Engine SQLAlchemy** — listener captura contención.
- **Heartbeat** — persiste `db_busy_count_5min`.
- **Notificaciones** — alerta daemon.db_contention.

## 3. Problem statement

La contención SQLite persistente (CLI↔daemon) indica un problema real de coordinación. Sin alerta, pasa desapercibida hasta que los timeouts se vuelven frecuentes. La alerta debe deduplicarse por flanco (no repetir en cada heartbeat).

## 4. Requirements

#### ADDED: Persistir contador en el heartbeat (§5.8/T19)
**After:** El heartbeat persiste `busy_count()` en `daemon_state.db_busy_count_5min` (helper T37 con commit interno). `daemon status` lo lee desde allí, nunca del proceso CLI (T19).

#### ADDED: Alerta daemon.db_contention con dedupe por flanco (§5.8)
**After:** Si `busy_count()` supera `DB_BUSY_TIMEOUT_ALERT_THRESHOLD` (default 20) en la ventana → emitir `daemon.db_contention` por Telegram; **dedupe por flanco** (solo al cruzar el umbral, no en cada heartbeat; re-emite al bajar y volver a subir).

#### ADDED: Confirmar integración (L-C5)
**After:** PRAGMA `busy_timeout=5000` primero, `journal_mode=WAL` después (L-C5) — confirmar que el listener y el PRAGMA coexisten (e01s04).

## 5. Solution and main flow

1. `core/daemon_state.py`: helper `persist_busy_count(count)` (T37).
2. `core/db.py` o daemon: lógica de alerta con dedupe por flanco.
3. Heartbeat: persistir + evaluar umbral.

## 6. Alternative flows / edge cases

- **Bajo umbral**: no alerta.
- **Cruza umbral**: alerta; no repite hasta bajar y volver.

## 7. Assumptions

- `record_busy`/`busy_count` (e02s04) con ventana rotativa.

## 8. Constraints

- Contador leído de daemon_state (T19).
- Alerta con dedupe por flanco.

## 9. Dependencies

- e02s04 (ventana rotativa, heartbeat), e01s04 (daemon_state).

## 10. Interfaces

- `core/daemon_state.py` → persist_busy_count.
- `core/db.py` → busy_count.
- Consumido por heartbeat (e02s03).

## 11. Test plan

- `tests/db/test_busy_handling.py`: listener captura, ventana rotativa, persistencia (T19), alerta con dedupe por flanco.

## 12. Data

- `daemon_state.db_busy_count_5min`.

## 13. Security considerations

- Sin secretos.

## 14. Performance

- Ventana rotativa O(n) acotada.

## 15. Operational concerns

- Alerta accionable: contención CLI↔daemon.

## 16. Risks

- **Alerta repetida**: dedupe por flanco.

## 17. Acceptance criteria

- [ ] Heartbeat persiste busy_count en daemon_state (T19/T37).
- [ ] Alerta daemon.db_contention al superar umbral (20) con dedupe por flanco.
- [ ] daemon status lee de daemon_state (T19).
- [ ] L-C5 confirmado (busy_timeout antes de WAL).
- [ ] Tests en `tests/db/` pasan.

## 18. Out of scope

- Backlog: reintentos con backoff más sofisticados.

## 19. Risks (detailed)

- **Dedupe por flanco**: test del cruce de umbral.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/db/` pasa.
- Tasks `status: passing` en `e07s04-tasks.yaml`.

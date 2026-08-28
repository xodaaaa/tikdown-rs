# Story e02s01 — Supervised tasks (create_supervised_task, registro, drenaje)

**type:** feat
**risk:** P0
**context:** infra
**BCPs:** 3
**status:** planned

## 1. Business narrative

Toda tarea de fondo del daemon (monitor, validación de cookies, backfill) pasa por un helper supervisado `create_supervised_task()` — nunca `asyncio.create_task` directo (principio §0.10). El helper registra las tareas, audita su resultado (incl. cancelación) y permite drenarlas en el apagado (T27/T28).

## 2. Actors

- **Daemon runner** — lanza y drena tareas supervisadas.
- **Jobs de APScheduler** — lanzan su trabajo como tareas supervisadas.
- **Apagado** — drena el registro (no el scheduler, T9).

## 3. Problem statement

Sin supervisión, una tarea de fondo que falla en silencio es un bug de producción. El drenaje del apagado no puede confiarse a `AsyncIOScheduler.shutdown(wait=True)` (T9: cancela en vez de esperar). El registro debe guardar referencias `Task` reales (T28) indexadas por `id(task)` (T30), y el `add_done_callback` debe ser síncrono (T1).

## 4. Requirements

#### ADDED: create_supervised_task() (principio §0.10)
**After:** `core/tasks.py` define `create_supervised_task(coro, name)` — única vía para tareas de fondo. Envuelve en try/except, loguea excepciones con contexto estructurado (stdlib logging, nunca structlog; interpolar nombre en mensaje, L-B4), registra la tarea en el registro.

#### ADDED: add_done_callback síncrono (T1)
**After:** El callback que audita el resultado es **síncrono** y lee `task.exception()` (incluye cancelación). Nunca un callback async (crea corrutina que nunca se ejecuta).

#### ADDED: Registro indexado por id(task) (T30)
**After:** El registro guarda `_task_refs` reales (referencias `Task`) indexadas por `id(task)` — nunca por nombre (colisión entre tareas con mismo nombre lógico).

#### ADDED: cancel_pending_tasks() (T28)
**After:** `cancel_pending_tasks(timeout)` cancela explícitamente las tareas pendientes del registro — el drenaje del apagado usa ESTO (T27/T28), no el scheduler (T9).

## 5. Solution and main flow

1. `core/tasks.py`: `create_supervised_task(coro, name)`.
2. Registro `_task_refs` por `id(task)`.
3. `add_done_callback` síncrono (T1) que audita y limpia del registro.
4. `cancel_pending_tasks(timeout)` (T28).

## 6. Alternative flows / edge cases

- **Tarea cancelada**: el callback audita la cancelación (T1).
- **Excepción en tarea**: logueada con contexto (L-B4).

## 7. Assumptions

- asyncio en Python 3.13.

## 8. Constraints

- Nunca `asyncio.create_task` directo fuera del helper.
- Callback síncrono (T1).
- Índice por `id(task)` (T30).

## 9. Dependencies

- e01s03 (logging).

## 10. Interfaces

- `core/tasks.py` → `create_supervised_task`, `cancel_pending_tasks`.
- Consumido por daemon runner (e02s03) y jobs (e02s03).

## 11. Test plan

- `tests/daemon/test_supervised.py`: crea/registra tareas, audita excepciones, drena (cancel_pending_tasks), callback síncrono.

## 12. Data

Ninguno.

## 13. Security considerations

- Sin datos sensibles en logs de auditoría.

## 14. Performance

- Registro O(1) por id(task).

## 15. Operational concerns

- Errores no silenciosos: toda excepción se loguea.

## 16. Risks

- **Callback async accidental**: T1 — test que verifica que el callback corre (audita el resultado).

## 17. Acceptance criteria

- [ ] `create_supervised_task(coro, name)` registra y audita.
- [ ] `add_done_callback` es síncrono (T1) y audita `task.exception()`.
- [ ] Registro indexado por `id(task)` (T30).
- [ ] `cancel_pending_tasks(timeout)` cancela (T28).
- [ ] Tests en `tests/daemon/test_supervised.py` pasan.
- [ ] Nunca `asyncio.create_task` directo fuera del helper.

## 18. Out of scope

- Runner del daemon (e02s03) que usa el helper.
- Jobs de APScheduler (e02s03).

## 19. Risks (detailed)

- **Drenaje ficticio**: el apagado usa cancel_pending_tasks (T27/T28), no shutdown(wait) (T9).

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/daemon/test_supervised.py` pasa.
- Tasks `status: passing` en `e02s01-tasks.yaml`.

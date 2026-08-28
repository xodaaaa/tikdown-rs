# Story e13s01: Estado 'paused' real + recogida automática + slot cross-proceso (T22)

## 1. Metadata

| Campo | Valor |
|-------|-------|
| story_id | e13s01 |
| epic | e13-backfill-paused |
| type | feat |
| risk | P1 |
| context | domain |
| bcps | 6 |
| delta | ADDED |

## 2. Título

Recogida automática de backfills pausados: `backfill.paused` como estado real + slot cross-proceso.

## 3. Problema

`backfill_status` ya tiene `'paused'` en el CHECK (reservado, §2) pero **sin productor**: los
backfills interrumpidos por crash/apagado vuelven a `'queued'` (F-10). No hay forma de pausar
un backfill por causa (red/disco) y reanudarlo automáticamente cuando la causa se resuelve.
Además, el slot de backfill es **por proceso** (`asyncio.Lock`), no una barrera cross-proceso real
(§10: limitación documentada residual).

## 4. Contexto

`services/backfill.py` tiene `run_backfill` (except CancelledError → `'queued'`), slot con
`asyncio.Lock` por proceso, y `collect_queued_backfills` (solo `'queued'`). El pacing del cooldown
global ya es cross-proceso vía `download_pacing_state` (T22: `UPDATE ... RETURNING` + singleton
con commit L-C6). El disco pausa descargas (`downloads_paused` en daemon_state) y la red tiene
`NetworkMonitor.network_available`. No hay job de recogida registrado en run.py.

## 5. Alcance

- Productor de `'paused'` en `run_backfill` (causa red/disco), con `pause_reason`.
- Tabla singleton `backfill_slot` cross-proceso (patrón T22).
- Reemplazar el `asyncio.Lock` por el slot SQLite.
- `collect_queued_backfills` recoge `'queued'` + `'paused'` reanudables.
- Job del daemon que llama a la recogida periódicamente.

## 6. Fuera de alcance

- Pausa manual del backfill vía CLI/Telegram (solo automática por causa).
- Priorización compleja (orden de llegada se mantiene).
- Persistencia del cursor en `paused` (ya persiste en la cuenta).

## 7. Stack y dependencias

- SQLite (WAL) — ya en uso; sin dependencias nuevas.
- Patrón T22 (`UPDATE ... RETURNING`, singleton con commit L-C6).
- **Sin dependencias nuevas.**

## 8. Diseño

```
run_backfill (except CancelledError):
  si downloads_paused o red offline → backfill_status='paused', pause_reason=...
  sino → 'queued' (F-10 crash)

backfill_slot (tabla singleton, T22):
  id=1, owner TEXT NULL, acquired_at
  acquire: UPDATE ... SET owner=:me WHERE id=1 AND owner IS NULL RETURNING owner  (CAS)
  release: UPDATE ... SET owner=NULL WHERE id=1 AND owner=:me

collect_queued_backfills (job del daemon):
  if not acquire_slot(): return
  if downloads_paused o red offline → no reanudar 'paused' (sigue pausado)
  recoge 'queued' + 'paused' (con causa resuelta) → run_backfill
```

## 9. Requisitos

### ADDED: Estado 'paused' real (productor)
**After:** `run_backfill` marca `backfill_status='paused'` (con `pause_reason`) cuando la
interrupción es por disco (`downloads_paused`) o red offline; `'queued'` en crash (F-10).

### ADDED: Slot cross-proceso (T22)
**After:** Tabla singleton `backfill_slot` con adquisición atómica CAS (`UPDATE ... RETURNING`);
el slot es visible para todos los procesos (daemon + CLI + bot), reemplaza el `asyncio.Lock`
por proceso.

### ADDED: Recogida de paused reanudables
**After:** `collect_queued_backfills` recoge `'queued'` **y** `'paused'` cuya causa se resolvió
(disco no pausado + red online); el job del daemon la ejecuta periódicamente.

## 10. Comportamiento

1. Backfill interrumpido con disco lleno → `'paused'` (reason=disk).
2. Backfill interrumpido con red caída → `'paused'` (reason=network).
3. Crash/apagado sin causa → `'queued'` (F-10, auto-reanudable).
4. El daemon escanea `'queued'` + `'paused'`; reanuda los que la causa resolvió.
5. Dos procesos (CLI + daemon) no pueden ejecutar backfills a la vez (slot CAS).

## 11. Pasos de implementación

1. Productor `'paused'` + `pause_reason` → verify: `uv run pytest tests/backfill/test_backfill_paused.py -q`
2. Tabla `backfill_slot` (T22) + migración → verify: `uv run pytest tests/backfill/test_backfill_paused.py -q`
3. Reemplazar `asyncio.Lock` por slot SQLite → verify: `uv run pytest tests/backfill/test_backfill_paused.py -q`
4. `collect_queued_backfills` incluye paused reanudables + job en run.py → verify: `uv run pytest tests/backfill/test_backfill_paused.py -q`
5. Tests F.I.R.S.T. → verify: `uv run pytest tests/backfill/ -q`

## 12. Script de verificación (step-by-step)

1. `uv run pytest tests/backfill/test_backfill_paused.py -q` → tests de paused pasan.
2. `uv run pytest tests/backfill/ -q` → todos los tests de backfill pasan.
3. `uv run pytest --cov=tikdown_rs --cov-report=term -q` → suite completa sin regresión.
4. `uv run ruff check . && uv run ruff format --check .` → lint OK.
5. `uv run tikdown-rs --version` → CLI intacta (smoke).

## 13. Criterios de aceptación

- [ ] `run_backfill` produce `'paused'` (con reason) en interrupción por disco/red; `'queued'` en crash.
- [ ] Tabla `backfill_slot` con adquisición CAS cross-proceso (T22).
- [ ] `collect_queued_backfills` recoge paused reanudables (disco + red OK).
- [ ] `reconcile_stale_backfills` no toca `'paused'`.
- [ ] Job del daemon registrado (max_instances=1, T44).
- [ ] Sin dependencias nuevas.

## 14. Definición de éxito

`tests/backfill/` pasa, slot cross-proceso verificado (dos procesos simulados, uno gana),
recogida de paused reanudables funciona, sin regresión en la suite completa.

## 15. Saliendo

- Rama `feat/e13-backfill-paused` vía kickoff-branch.
- Commits Conventional Commits separados RED/GREEN.

## 16. Riesgos

| Riesgo | Detección |
|--------|-----------|
| CAS del slot no atómico (dos procesos ganan) | Test con dos adquisiciones concurrentes |
| `paused` vuelve a ser terminal para el cursor | Test de cursor sobre paused |
| Recogida reanuda paused con causa activa | Test con disco paused |
| Migración rompe el esquema | Test de migración |

## 17. Criterios de aceptación (checklist)

- [ ] Story spec en `specs/epics/e13-backfill-paused/e13s01-backfill-paused.md`
- [ ] Tasks con `status: failing` (no pre-marcados)
- [ ] Tests en `tests/backfill/test_backfill_paused.py`

## 18. Seguimiento

- Estado: `failing` → `passing` tras verify-work.
- `state.yaml` `active_flow` → `build_epic`.

## 19. Notas

- §2: `'paused'` reservado en el CHECK, ahora con productor real.
- F-10: crash → `'queued'` (conservado); pausa por causa → `'paused'`.
- T22: patrón de coordinación cross-proceso de `download_pacing_state` reutilizado para el slot.
- El slot cross-proceso cierra la limitación documentada de §10 (overlap CLI/daemon).

## 20. Riesgo (técnico)

P1 — lógica de estado + coordinación cross-proceso; toca el esquema (tabla nueva) y el flujo de
backfill. El CAS atómico es la pieza crítica (T22).

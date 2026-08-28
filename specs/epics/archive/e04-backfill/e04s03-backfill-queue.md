# Story e04s03 — Backfill queue + cancelación + retry-failed

**type:** feat
**risk:** P0
**context:** domain
**BCPs:** 10
**status:** planned

## 1. Business narrative

El backfill encolado para el daemon (slot único §10), la cancelación cooperativa real (T21), y `retry-failed` con techo de reintentos (T58/T63) y sin penalización por fallos de red (T64). El job de recogida propaga el canal de eventos (T75).

## 2. Actors

- **Usuario** — `backfill run --queue`, `backfill cancel`, `backfill retry-failed`.
- **Daemon** — recoge `queued` con su slot único (F-10).
- **Motor** — descarga (e04s01).

## 3. Problem statement

Sin slot único, dos backfills compiten. Sin propagación del canal (T75), los eventos se pierden. Sin techo de reintentos (T58/T63), un transitorio persiste infinito. Los fallos de red no deben penalizar (T64).

## 4. Requirements

#### ADDED: Slot único de backfill (§10)
**After:** `backfill_slot_busy()` — adquisición no bloqueante (`if lock.locked(): return False; await lock.acquire()`). El job de recogida comprueba el slot antes de crear la tarea (F-10).

#### ADDED: Recogida de queued con propagación de canal (T75)
**After:** `collect_queued_backfills()` — recoge backfills `queued`; **propaga `on_event=on_event` a `run_backfill`** (T75: sin canal, los eventos van a None).

#### ADDED: Transición history→monitor en la misma transacción (T59)
**After:** Al completar el backfill con `monitor_after_backfill=1`, la transición se ejecuta **en la misma transacción** que el completado (`UPDATE ... WHERE mode='history' AND monitor_after_backfill=1 AND backfill_status='completed'`); reconciliación en arranque.

#### ADDED: Cancelación real (T21)
**After:** `backfill cancel @user` marca `cancelled`; el worker relee periódicamente y detiene cooperativamente; re-ejecución retoma desde el cursor.

#### ADDED: retry-failed (§3)
**After:** `retry-failed @user` — reintenta `status='failed'`, descartando primero la entrada del archive. `@user` obligatorio; `--all` con resumen previo (vídeos × cuentas, duración estimada) + confirmación.

#### ADDED: Techo de reintentos (T58) + presupuesto de tiempo (T63) + red sin penalizar (T64)
**After:** `MAX_VIDEO_RETRY_COUNT` (5) alcanzado → `failed`/`transient` + `download.retry_exhausted` (T58). `MAX_VIDEO_TOTAL_TIME_SECONDS` (900) agotado → mismo (T63). Fallos de red NO incrementan retry_count ni consumen presupuesto (T64).

## 5. Solution and main flow

1. `services/backfill.py`: slot (F-10), collect_queued (T75), cancel (T21), retry_failed.
2. `services/videos.py`: handle_download_result con T58/T63/T64.
3. `cli/backfill.py`: run --queue, cancel, retry-failed (--all).

## 6. Alternative flows / edge cases

- **Slot ocupado**: adquisición no bloqueante → False.
- **Sin canal**: T75 (propagación explícita).
- **Red caída**: T64 (sin penalización).

## 7. Assumptions

- Motor/archive (e04s01), run_backfill (e04s02).

## 8. Constraints

- Slot único no bloqueante (§10).
- Transición en misma transacción (T59).
- Techo de reintentos (T58/T63).
- Red no penaliza (T64).

## 9. Dependencies

- e04s01 (motor), e04s02 (run_backfill, cursor).

## 10. Interfaces

- `services/backfill.py` → collect_queued_backfills, cancel_backfill, retry_failed.
- `services/videos.py` → handle_download_result.
- `cli/backfill.py` → run --queue, cancel, retry-failed.

## 11. Test plan

- `tests/backfill/test_backfill_queue.py`: slot, collect_queued (T75), T59.
- `tests/backfill/test_backfill_cancel.py`: cancel (T21), retry-failed (T58/T63/T64).

## 12. Data

- `monitored_accounts` (status, cursor), `videos` (retry_count, error_category).

## 13. Security considerations

- Sin secretos; confirmación en --all.

## 14. Performance

- Slot único evita competencia; presupuesto evita head-of-line blocking (T63).

## 15. Operational concerns

- --all requiere resumen + confirmación (volumen).

## 16. Risks

- **Eventos perdidos**: T75 (propagación).

## 17. Acceptance criteria

- [ ] `backfill_slot_busy()` no bloqueante (F-10).
- [ ] `collect_queued_backfills()` propaga on_event (T75).
- [ ] Transición history→monitor en misma transacción (T59).
- [ ] `backfill cancel` cooperativo (T21).
- [ ] `retry-failed @user`; `--all` con resumen + confirmación.
- [ ] T58/T63 (techo → failed/transient + retry_exhausted); T64 (red no penaliza).
- [ ] Tests en `tests/backfill/` pasan.

## 18. Out of scope

- Circuit breaker (e07).
- Cookies (e05).

## 19. Risks (detailed)

- **T75**: propagar canal en collect_queued.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/backfill/` pasa.
- Tasks `status: passing` en `e04s03-tasks.yaml`.

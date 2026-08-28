# Story e04s02 — Backfill foreground + cursor (backfill run)

**type:** feat
**risk:** P0
**context:** domain
**BCPs:** 10
**status:** planned

## 1. Business narrative

El backfill descarga el histórico de una cuenta. Esta story cubre el backfill **foreground** (proceso CLI con barra de progreso): cursor estricto por `upload_date` (§10), deduplicación doble (archive + tabla), contabilidad de progreso (F-09), robustez ante interrupciones (F-10), cancelación cooperativa (T21) y cookies obligatorias (F-01).

## 2. Actors

- **Usuario** — `tikdown-rs backfill run @user` (foreground).
- **Daemon** — recoge backfills encolados (e04s03).
- **Motor** — descarga vídeos (e04s01).

## 3. Problem statement

El backfill debe reanudar desde donde quedó (cursor por upload_date), no duplicar (archive), contar progreso correctamente (F-09), sobrevivir a interrupciones (F-10) y cancelarse limpiamente (T21).

## 4. Requirements

#### ADDED: Cursor estricto por upload_date (§10, L-F1/L-F2)
**After:** Cursor por `upload_date` con comparación **estrictamente `<`** (nunca `==`). `scope_cursor` (snapshot para break) separado del `cursor` móvil (L-F1). `upload_date` ausente → fallback al cursor anterior **actualizado** (L-F2). El cursor **solo avanza en estado terminal** (downloaded/failed/skipped; NO cancelled).

#### ADDED: Contabilidad de progreso (F-09)
**After:** `backfill_total` persistido al iniciar cada pasada; `backfill_done` acumulativo (los ya archivados se saltan sin contar); `done` cuenta `skipped` también (converge con total).

#### ADDED: Robustez ante interrupciones (F-10)
**After:** Listado del feed DENTRO del try catástrofe; `except asyncio.CancelledError` (BaseException) → backfill vuelve a `queued`; `reconcile_stale_backfills()` en arranque.

#### ADDED: Cancelación cooperativa (T21, L-F5/L-F6/L-F7)
**After:** Relectura periódica del estado; persistencia del cursor con **UPDATE condicional `WHERE backfill_status='backfilling'`** — rowcount 0 = cancelación detectada (L-F5/L-F6); retorno temprano del outcome `cancelled`; CHECK incluye `cancelled` (L-F7).

#### ADDED: Cookies obligatorias (F-01)
**After:** El backfill descarga CON cookies — `get_working_cookie()` al inicio; aborta con `backfill.no_cookies` si no hay ninguna.

#### ADDED: Deduplicación doble + transición --then-monitor
**After:** `--download-archive` (yt-dlp) + tabla `download_archive` como fuente consultable; al completar: transición consumible history→monitor (§10).

## 5. Solution and main flow

1. `services/backfill.py`: `run_backfill()` (foreground).
2. Helpers de cursor (scope vs móvil, L-F1).
3. `services/videos.py`: handle_download_result (parcial).
4. CLI `backfill run/status`.

## 6. Alternative flows / edge cases

- **upload_date ausente**: fallback cursor anterior (L-F2).
- **Interrupción**: CancelledError → queued (F-10).
- **Cancelación**: rowcount 0 → outcome cancelled (L-F5/L-F6).
- **Sin cookies**: aborta no_cookies (F-01).

## 7. Assumptions

- Motor (e04s01), archive (e04s01), modelos (e01s04).

## 8. Constraints

- Cursor `<` nunca `==` (§10).
- Cursor solo avanza en estado terminal.
- UPDATE condicional (T21/L-F5).
- Cookies obligatorias (F-01).

## 9. Dependencies

- e04s01 (motor, archive, pacing), e03s01 (accounts), e01s04 (modelos).

## 10. Interfaces

- `services/backfill.py` → `run_backfill`.
- `cli/backfill.py` → run/status.
- Consumido por el daemon (e04s03).

## 11. Test plan

- `tests/backfill/test_backfill_fg.py`: cursor §10, L-F1/L-F2, F-09, F-10, T21, L-F5/L-F6, F-01.
- `tests/backfill/test_backfill_cli.py`: run/status.

## 12. Data

- `monitored_accounts` (cursor, total, done, status), `videos`, `download_archive`.

## 13. Security considerations

- Cookies nunca en logs.

## 14. Performance

- Cursor evita re-descargas; done/total converge.

## 15. Operational concerns

- Barra de progreso rich (foreground).

## 16. Risks

- **Cursor ==**: perder/repetir vídeos en el borde (§10).

## 17. Acceptance criteria

- [ ] Cursor `<` estricto; scope_cursor separado (L-F1); fallback upload_date ausente (L-F2).
- [ ] Cursor solo avanza en estado terminal (downloaded/failed/skipped).
- [ ] F-09: total al iniciar, done acumulativo (incl. skipped).
- [ ] F-10: feed en try catástrofe; CancelledError → queued; reconcile_stale_backfills.
- [ ] T21/L-F5/L-F6: UPDATE condicional + rowcount 0 → cancelled.
- [ ] F-01: aborta no_cookies sin cookies.
- [ ] Deduplicación doble (archive + tabla).
- [ ] Tests en `tests/backfill/` pasan.

## 18. Out of scope

- Backfill encolado al daemon (e04s03).
- retry-failed (e04s03).

## 19. Risks (detailed)

- **Cursor ==**: comparación estrictamente <.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/backfill/` pasa.
- Tasks `status: passing` en `e04s02-tasks.yaml`.

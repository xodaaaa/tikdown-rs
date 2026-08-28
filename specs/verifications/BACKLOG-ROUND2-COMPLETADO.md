# Backlog Round 2 Completado — TikDown-rs v0.3.0

## Fecha de finalización

2026-08-28 (sesión de backlog round 2 cerrada)

## Resumen de los 3 items

| # | Item | Epic/Story | Bump | Estado |
|---|------|-----------|------|--------|
| 1 | **Recogida automática de backfills pausados** — estado `paused` real + slot cross-proceso | e13s01 | minor | ✅ Liberado |
| 2 | **Logs a archivo rotado** — JSON por tamaño/tiempo, retención configurable | e14s01 | minor | ✅ Liberado |
| 3 | **Métricas y healthcheck más ricos** — cookies, disco, errores, contención | e15s01 | minor | ✅ Liberado |

## Métricas (verificadas en vivo)

| Check | Resultado |
|-------|-----------|
| Tests | **247 passed** (219 → 247, +28 nuevos) |
| Lint | **All checks passed** (ruff check + format) |
| Cobertura | 63% total (puntos calientes > 82%) |
| Commits | 12 commits convencionales (3 test + 3 feat + 3 chore + 3 docs) |
| Migraciones | 1 nueva (e13s01_backfill_slot) |

## Detalle por item

### 1. Backfills pausados (e13s01) — `feat(backfill)`
- §2: `backfill_status='paused'` ahora tiene **productor real** — `status_after_interruption()`
  decide: disco pausado o red offline → `'paused'` (con `pause_reason`); crash → `'queued'` (F-10)
- **Slot cross-proceso real (T22)**: tabla singleton `backfill_slot` con adquisición atómica CAS
  (`UPDATE ... SET owner=:me WHERE owner IS NULL RETURNING`) — visible para daemon + CLI + bot;
  reemplaza el `asyncio.Lock` por proceso
- **F-10**: `collect_queued_backfills()` recoge `'queued'` **y** `'paused'` reanudables
  (condición: red online + disco no pausado); `reconcile_stale_backfills` NO toca `'paused'`
- Job del daemon `backfill-collect` (60s, max_instances=1 T44)
- Migración Alembic `e13s01_backfill_slot` (+ `backfill_pause_reason`)

### 2. Logs rotados (e14s01) — `feat(logging)`
- §1/principio 8: JSON a stdout se MANTIENE; archivo rotado adicional
- `RotatingFileHandler` (por tamaño, 10MB) o `TimedRotatingFileHandler` (por tiempo, midnight)
- Config por entorno: `LOG_FILE_PATH`, `LOG_FILE_MAX_BYTES`, `LOG_FILE_BACKUP_COUNT`, `LOG_FILE_WHEN`
- Mismo `JsonFormatter` en stdout y archivo (JSON consistente)
- **T72**: la reaplicación tras migraciones respeta la config del archivo (desde Settings)

### 3. Métricas/healthcheck (e15s01) — `feat(status)`
- §3: `daemon status` muestra vía `collect_status()` (lógica en `services/`, no cli/):
  cookies (válidas/expirando), disco (libre %, umbral, alerta), últimos errores (top 5 videos
  failed con timestamp/categoría/cuenta), contención `db_busy_count_5min` (T19)
- §3/T50: `daemon healthcheck` ampliado — heartbeat fresco + ≥1 cookie válida + disco con espacio
  + sin errores críticos recientes (definitive < 24h); exit 0/1
- §22.1: ligero — sin validaciones de red ni selfcheck pesado (corre cada ~30s)
- Últimos errores derivados de `videos failed` (sin tabla nueva)

## Publicación

- Repositorio: **privado** — https://github.com/xodaaaa/tikdown-rs
- Tag: **v0.3.0** en el remoto (commit `ad0880d`, cierre del backlog round 2)
- Rama `master` sincronizada, working tree limpio

## Estado final

- `specs/state.yaml`: `release.last_tag: v0.3.0`, `active_flow: null`, `next_skill: survey-context`
- Epics e13/e14/e15 archivados en `specs/epics/archive/`
- Historial de tags: `v0.1.0` (MVP) → `v0.2.0` (CI + paginación + supervisión) → `v0.3.0` (backfills + logs + métricas)
- Siguiente paso: `survey-context` para el próximo ciclo de desarrollo

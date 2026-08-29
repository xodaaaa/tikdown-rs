# VIBE Round 2 — v0.3.2

> **Reason for existence:** Registro de la ronda 2 de pruebas pseudo-producción (rama `vibe`): reanudación del backfill de `rosary657`, el flag `--queue` para re-encolar, y el bug #17 que impedía el backfill automático del daemon.

**Ronda:** vibe round 2 · **Fecha:** 2026-08-29 · **Tag:** `v0.3.2` · **PR:** [#2](https://github.com/xodaaaa/tikdown-rs/pull/2) · **Merge:** `4242bc1`

---

## Qué se probó

1. Reanudación del backfill de `rosary657` (estado `completed` → foreground `backfill run`)
2. Diagnóstico: pipeline correcto (3 mp4 reales verificados en ronda 1), pero **rate-limit de TikTok** en la IP de pruebas → 0 descargas en la pasada final
3. Necesidad: re-encolar backfills terminados para que el daemon los reintente con pacing T62 + retry

## Cambios de la ronda

### 1. `feat(cli): flag --queue` en `backfill run`
- `completed`/`failed`/`cancelled`/`paused`/`idle` → `queued` (el daemon lo recoge cada 60s con pacing T62)
- `backfilling` → rechaza (`ya está en curso`)
- Cuenta inexistente → `ValueError` con mensaje claro
- Lógica en `services/backfill.py::requeue_backfill()` (regla de oro: services, no CLI)

### 2. `fix(daemon): bug #17` — backfill-collect con AsyncEngine
- **Síntoma:** `task.failed name=backfill-collect exc=AttributeError("'AsyncEngine' object has no attribute 'extract_profile'")`
- **Causa:** el job `_backfill_collect_job` del daemon pasaba el `AsyncEngine` SQLAlchemy como motor de descarga a `collect_queued_backfills`, que espera un `YtDlpEngine`
- **Impacto:** TODO el backfill automático del daemon estaba roto — nunca se activó antes porque ninguna cuenta llegaba a `queued` (solo con `--queue` ahora sí)
- **Fix:** construir `YtDlpEngine(cookies_blob=blob)` real con la cookie working descifrada (mismo patrón que la CLI, F-01/F-15)

## Tests

| Test | Verifica |
|------|----------|
| `test_requeue_completed_a_queued` | `completed` → `queued` |
| `test_requeue_backfilling_rechaza` | `backfilling` → `rejected`, no cambia |
| `test_requeue_failed_a_queued` | `failed` → `queued` |
| `test_requeue_cuenta_no_existe` | cuenta inexistente → `ValueError` |

Preflight: **251 tests passed** (247 + 4 nuevos), ruff OK, build OK.

## Evidencia end-to-end (Docker)

```bash
# Re-encolar
$ docker compose exec tikdown-rs tikdown-rs backfill run rosary657 --queue
OK backfill rosary657 re-encolado (era completed) → queued

# 60s después (ciclo del daemon): el daemon recogió el queued, ejecutó y completó
$ docker compose exec tikdown-rs tikdown-rs backfill status rosary657
rosary657: status=completed done=0/4 cursor=00000000

# Logs: job _schedule_backfill cada 60s SIN task.failed (bug #17 resuelto)
{"level":"INFO","logger":"apscheduler.executors.default","message":"Job \"..._schedule_backfill\" executed successfully"}
```

**Nota honesta:** `done=0` — las descargas siguen bloqueadas por rate-limit de TikTok, pero el pipeline automático ahora funciona y reintenta en cada `--queue` (o cuando TikTok levante el bloqueo).

## Estado final del sistema

- **master** = `4242bc1` (squash PR #2 + state v0.3.2)
- **vibe** = `4242bc1` (sincronizada, lista para próxima ronda)
- **Tag:** `v0.3.2` pusheado
- **Daemon:** healthy, job backfill cada 60s, heartbeat 10s, bot Telegram conectado

## Verify

```bash
test -f specs/verifications/VIBE-ROUND2.md && git tag -l "v0.3.2" && echo OK
```

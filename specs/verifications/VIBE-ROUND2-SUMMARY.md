# VIBE Round 2 — Summary de cierre

> **Reason for existence:** Cierre de la ronda 2 de pruebas pseudo-producción (rama `vibe`). Registro del flag `--queue`, el bug #17, y el estado del rate-limit de TikTok para retomar en otra sesión.

**Ronda:** vibe round 2 · **Cierre:** 2026-08-28 · **Tag:** `v0.3.2` · **PR:** [#2](https://github.com/xodaaaa/tikdown-rs/pull/2) · **Merge:** `4242bc1`

---

## Resumen de la ronda 2

La ronda 2 reanudó el backfill de `rosary657` sobre la infraestructura de 16 fixes de la ronda 1. El pipeline completo funciona (listado del feed, descarga mp4 verificada en ronda 1), pero TikTok mantiene **rate-limit sobre la IP de pruebas** — todas las descargas de la pasada final fallaron con `Unexpected response from webpage request`.

## Flag `--queue` implementado

`backfill run <user> --queue` re-encola backfills terminados para que el daemon los reintente automáticamente:

| Estado previo | Acción |
|---------------|--------|
| `completed` / `failed` / `cancelled` / `paused` / `idle` | → `queued` (el daemon lo recoge cada 60s con pacing T62) |
| `backfilling` | Rechaza (`ya está en curso`) |
| Cuenta inexistente | `ValueError` con mensaje claro |

Lógica en `services/backfill.py::requeue_backfill()` (regla de oro: services, no CLI). 4 tests nuevos (251 total).

## Bug #17 corregido — daemon backfill automático

- **Síntoma:** `task.failed name=backfill-collect exc=AttributeError("'AsyncEngine' object has no attribute 'extract_profile'")`
- **Causa:** el job `_backfill_collect_job` pasaba el `AsyncEngine` SQLAlchemy como motor de descarga a `collect_queued_backfills`, que espera un `YtDlpEngine`
- **Impacto:** TODO el backfill automático del daemon estaba roto — nunca se activó porque ninguna cuenta llegaba a `queued` hasta la ronda 2
- **Fix:** construir `YtDlpEngine(cookies_blob=blob)` real con la cookie working descifrada (F-01/F-15)

**Evidencia:** tras `--queue`, el daemon recogió el backfill en ≤60s, lo ejecutó, y `task.failed` desapareció de los logs.

## Estado del rate-limit de TikTok

- **Standby:** la IP de pruebas está bloqueada temporalmente por TikTok (descargas fallan con `Unexpected response from webpage request`).
- **Retomada prevista:** 24-48 horas (recuperación típica del rate-limit).
- **Acción al retomar:** `git checkout vibe` → `docker compose build && up -d` → `tikdown-rs backfill run rosary657 --queue`. El daemon reintentará automáticamente con pacing T62 + retry.
- **No hay trabajo pendiente de código:** el pipeline es correcto (3 mp4 reales verificados en ronda 1). Solo espera de IP.

## Enlaces

- PR #2: https://github.com/xodaaaa/tikdown-rs/pull/2
- Tag `v0.3.2`: https://github.com/xodaaaa/tikdown-rs/releases/tag/v0.3.2
- Detalle completo: [`VIBE-ROUND2.md`](./VIBE-ROUND2.md)

## Verify

```bash
test -f specs/verifications/VIBE-ROUND2-SUMMARY.md && git tag -l "v0.3.2" && echo OK
```

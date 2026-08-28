# Story e15s01: daemon status ampliado + healthcheck con cookies/disco/errores (T19/T50)

## 1. Metadata

| Campo | Valor |
|-------|-------|
| story_id | e15s01 |
| epic | e15-metricas-healthcheck |
| type | feat |
| risk | P2 |
| context | domain |
| bcps | 5 |
| delta | ADDED |

## 2. Título

Métricas y healthcheck más ricos: cookies, disco, últimos errores, contención detallada.

## 3. Problema

`daemon status` muestra solo heartbeat/selfcheck/contención básica. `daemon healthcheck` (T50)
es solo frescura de heartbeat. §3 exige: estado de cookies (válidas/expirando), estado de disco
(espacio libre, umbral), últimos errores (timestamp, categoría, cuenta), contención SQLite
detallada; y un healthcheck que considere cookies válidas, disco con espacio y sin errores
críticos recientes.

## 4. Contexto

`cli/daemon.py` tiene `status` (ya lee `db_busy_count_5min` de daemon_state, T19) y `healthcheck`
(solo heartbeat fresco, T50/R10). `services/` no tiene módulo de status. `Video` (models) tiene
`status='failed'`, `error_category`, `error_message`, `account_id`, `updated_at` — fuente de
"últimos errores". `Cookie` tiene `validation_state` + `expiration_date`. `core/disk.free_percent`
ya existe (T69). El contador `db_busy_count_5min` está en daemon_state (§5.8).

## 5. Alcance

- `services/status.py` con `collect_status(session, settings)` (lógica en services, no cli/).
- `daemon status` usa collect_status: cookies, disco, últimos errores, contención.
- `daemon healthcheck` ampliado: heartbeat + cookie válida + disco OK + sin errores críticos.
- Sin validaciones de red en healthcheck (T50/§22.1 — ligero).

## 6. Fuera de alcance

- Persistencia de un registro de errores propio (se deriva de `videos failed`).
- Notificaciones de healthcheck (solo exit code).
- Métricas push a servicios externos.

## 7. Stack y dependencias

- `services/status.py` (nuevo), `core/disk.free_percent`, modelos Video/Cookie/DaemonState.
- **Sin dependencias nuevas.**

## 8. Diseño

```
services/status.py:
  collect_status(session, settings) -> dict:
    cookies: {valid: N, invalid: N, expiring: N}
    disk: {free_percent, warning_threshold, alert: bool}
    recent_errors: [{timestamp, category, account, message}]  (top 5 videos failed)
    contention: {db_busy_count_5min}  (T19, de daemon_state)

cli/daemon.py status:
  usa collect_status → imprime todo

cli/daemon.py healthcheck:
  fresh heartbeat (T50) AND
  >= 1 cookie válida AND
  free_percent > threshold AND
  sin errores críticos recientes (videos failed < 24h, count < umbral)
  → exit 0/1 (sin red, sin selfcheck pesado)
```

## 9. Requisitos

### ADDED: collect_status en services
**After:** `services/status.py` reúne cookies (válidas/expirando), disco (libre, umbral), últimos
errores (videos failed top 5), contención (T19). Lógica en services, nunca cli/.

### ADDED: daemon status ampliado
**After:** `daemon status` muestra cookies, disco, últimos errores y contención detallada.

### ADDED: healthcheck ampliado
**After:** `daemon healthcheck` considera heartbeat fresco (T50) + cookie válida + disco con
espacio + sin errores críticos recientes. Ligero (sin red, §22.1). Exit 0/1.

## 10. Comportamiento

1. `daemon status` → muestra cookies (N válidas, N expirando), disco (X% libre, umbral Y%),
   últimos errores (top 5 con timestamp/categoría/cuenta), contención.
2. `daemon healthcheck` con heartbeat fresco + cookie válida + disco OK → exit 0.
3. Sin cookie válida → exit 1.
4. Disco bajo umbral → exit 1.
5. Errores críticos recientes (videos failed < 24h) → exit 1 (si count >= umbral).

## 11. Pasos de implementación

1. `services/status.py` collect_status → verify: `uv run pytest tests/status/test_status.py -q`
2. `daemon status` usa collect_status → verify: `uv run pytest tests/status/test_status.py -q`
3. `daemon healthcheck` ampliado → verify: `uv run pytest tests/status/test_status.py -q`
4. Tests F.I.R.S.T. → verify: `uv run pytest tests/status/ -q`

## 12. Script de verificación (step-by-step)

1. `uv run pytest tests/status/test_status.py -q` → tests pasan.
2. `uv run pytest tests/status/ -q` → todos los tests de status pasan.
3. `uv run pytest --cov=tikdown_rs --cov-report=term -q` → suite completa sin regresión.
4. `uv run ruff check . && uv run ruff format --check .` → lint OK.
5. `uv run tikdown-rs --version` → CLI intacta (smoke).

## 13. Criterios de aceptación

- [ ] `services/status.py` con `collect_status` (cookies, disco, errores, contención).
- [ ] `daemon status` muestra las 4 métricas.
- [ ] `daemon healthcheck` considera cookie válida + disco + errores (además del heartbeat T50).
- [ ] Healthcheck ligero (sin red, §22.1).
- [ ] Contención leída de daemon_state (T19).
- [ ] Sin dependencias nuevas.

## 14. Definición de éxito

`tests/status/` pasa, `daemon status` muestra las métricas, `healthcheck` responde según
cookies/disco/errores, sin regresión.

## 15. Saliendo

- Rama `feat/e15-metricas-healthcheck` vía kickoff-branch.
- Commits Conventional Commits separados RED/GREEN.

## 16. Riesgos

| Riesgo | Detección |
|--------|-----------|
| Healthcheck pesado (red/selfcheck) | Test de que no llama a red |
| Contención desde proceso CLI (T19) | Test de que lee daemon_state |
| Disco no mockeado (T69) | Test con shutil.disk_usage inyectado |
| Cookies expirando mal calculadas | Test con expiration_date próxima |

## 17. Criterios de aceptación (checklist)

- [ ] Story spec en `specs/epics/e15-metricas-healthcheck/e15s01-metricas-healthcheck.md`
- [ ] Tasks con `status: failing` (no pre-marcados)
- [ ] Tests en `tests/status/test_status.py`

## 18. Seguimiento

- Estado: `failing` → `passing` tras verify-work.
- `state.yaml` `active_flow` → `build_epic`.

## 19. Notas

- §3: daemon status muestra cookies, disco, errores, contención.
- T50: healthcheck = frescura de heartbeat (MVP) → ahora expandido con cookies/disco/errores.
- T19: contención se lee de daemon_state, nunca del proceso CLI.
- §22.1: healthcheck ligero (sin validaciones de red en cada intervalo).
- Los "últimos errores" se derivan de `videos failed` (sin tabla nueva).

## 20. Riesgo (técnico)

P2 — presentación de métricas + healthcheck; sin lógica de negocio nueva. El healthcheck debe
mantenerse ligero (R10/T50).

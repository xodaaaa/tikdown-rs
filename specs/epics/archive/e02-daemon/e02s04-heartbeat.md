# Story e02s04 — Heartbeat persistido + daemon status/healthcheck

**type:** feat
**risk:** P0
**context:** infra
**BCPs:** 7
**status:** planned

## 1. Business narrative

El daemon persiste su heartbeat en `daemon_state` (la coordinación CLI↔daemon es vía SQLite). Los comandos `daemon status`, `healthcheck` y `stop` leen ese estado. El healthcheck de Docker usa solo la frescura del heartbeat (T50); el contador de contención se lee desde `daemon_state`, nunca del proceso CLI (T19).

## 2. Actors

- **Daemon** — persiste heartbeat, monitor_running, stop_requested, contención (§5.8).
- **CLI** — `daemon status/stop/healthcheck`.
- **Docker HEALTHCHECK** — `daemon healthcheck` cada ~30s (R10).

## 3. Problem statement

Sin heartbeat persistido, `daemon status` no puede ver el estado del daemon (solo DB). El healthcheck de Docker debe ser barato y no migrar (R10/T50). El contador de contención debe leerse desde `daemon_state`, no del proceso CLI (T19).

## 4. Requirements

#### ADDED: Heartbeat persistido (§5.1, F-08)
**After:** El job de heartbeat actualiza `last_heartbeat_at`, `daemon_pid`, y aplica en caliente `monitor_running`/`stop_requested` escritos por otros procesos (T19: el estado en memoria no es visible; el heartbeat es la fuente). Persiste `db_busy_count_5min` con ventana rotativa de 5 min (§5.8) y `last_selfcheck_at`/`last_selfcheck_ok`.

#### ADDED: Contención SQLite con ventana rotativa (§5.8)
**After:** El listener `handle_error` de db.py incrementa un contador en memoria (con marca de tiempo); el heartbeat lo persiste en `db_busy_count_5min` usando una **ventana rotativa real de 5 minutos**. Si supera `DB_BUSY_TIMEOUT_ALERT_THRESHOLD` (default 20) → alerta `daemon.db_contention` (dedupe por flanco).

#### ADDED: daemon status (T19, T66)
**After:** `daemon status` muestra: estado del daemon + monitor + heartbeat + `last_selfcheck_ok` + tareas supervisadas activas + hilos zombis de yt-dlp (T66) + **contador de contención leído de `daemon_state`**, nunca del proceso CLI propio (T19).

#### ADDED: daemon healthcheck (T50, R10)
**After:** `daemon healthcheck` = daemon vivo si **heartbeat fresco** (`last_heartbeat_at` ≤ 3 × `HEARTBEAT_INTERVAL_SECONDS` configurado, T50). Exit 0/1. **NO ejecuta migraciones ni toma `.migrate.lock`** (R10). NO ejecuta selfcheck completo.

#### ADDED: daemon stop (T37)
**After:** `daemon stop` escribe `stop_requested=true` con helper de commit interno (T37).

## 5. Solution and main flow

1. Heartbeat job en daemon/run.py (ya en e02s03) → extender con contención + selfcheck persistido.
2. db.py: listener con ventana rotativa (§5.8).
3. `daemon/jobs.py` o helpers: persistir contención.
4. Comandos CLI daemon (vía cli/common.py T18): status, stop, healthcheck.

## 6. Alternative flows / edge cases

- **Esquema ausente en healthcheck**: reporta unhealthy (exit 1) sin migrar (R10).
- **Contención en ventana**: ventana rotativa real, no acumulada.

## 7. Assumptions

- `db_busy_count_5min` en daemon_state (ya en modelo, e01s04).

## 8. Constraints

- Contención leída de daemon_state (T19).
- Healthcheck: solo frescura de heartbeat (T50), sin migrar (R10).
- Heartbeat es la fuente de coordinación.

## 9. Dependencies

- e02s03 (runner, helpers T37), e01s04 (modelos), e02s01 (tasks).

## 10. Interfaces

- `daemon/run.py` heartbeat → persiste.
- `cli/daemon.py` → status/stop/healthcheck.
- Consumido por Docker HEALTHCHECK.

## 11. Test plan

- `tests/daemon/test_heartbeat.py`: heartbeat persiste, ventana rotativa, contención.
- `tests/daemon/test_commands.py`: status (T19), healthcheck (T50/R10), stop (T37).

## 12. Data

- `daemon_state` (heartbeat, contención, selfcheck).

## 13. Security considerations

- Healthcheck barato sin migrar (R10) — menos superficie.

## 14. Performance

- Healthcheck solo lee heartbeat (rápido).

## 15. Operational concerns

- Docker `--start-period` cubre selfcheck de arranque (T50).

## 16. Risks

- **Contención leída del proceso CLI (siempre 0)**: T19.

## 17. Acceptance criteria

- [ ] Heartbeat persiste last_heartbeat_at, contención (ventana rotativa §5.8), selfcheck.
- [ ] `daemon status` lee contención de daemon_state (T19), muestra tareas + zombis (T66).
- [ ] `daemon healthcheck` = frescura de heartbeat ≤ 3×intervalo (T50); sin migrar (R10).
- [ ] `daemon stop` escribe stop_requested con commit interno (T37).
- [ ] Tests en `tests/daemon/test_heartbeat.py` y `test_commands.py` pasan.

## 18. Out of scope

- CLI completo (e08).
- Contenido real de jobs (monitor, cookies) — e03+.
- hilos zombis reales de yt-dlp (T66) — e04.

## 19. Risks (detailed)

- **T19**: contención siempre desde daemon_state.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/daemon/test_heartbeat.py tests/daemon/test_commands.py` pasa.
- Tasks `status: passing` en `e02s04-tasks.yaml`.

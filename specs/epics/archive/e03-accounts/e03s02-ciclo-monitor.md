# Story e03s02 — Ciclo de monitor (descubrimiento de vídeos nuevos)

**type:** feat
**risk:** P0
**context:** domain
**BCPs:** 8
**status:** planned

## 1. Business narrative

El monitor es el ciclo que descubre y encola vídeos nuevos de cuentas en modo `monitor`. Respeta el throttle de 30s por cuenta (L-G1: NULL siempre se comprueba; <30s se salta), arranca siempre detenido (§5.1/T60), y solo detecta vídeos nuevos — **no arranca el backfill** (§10).

## 2. Actors

- **Daemon** — ejecuta el ciclo del monitor (job supervisado).
- **Usuario** — `monitor start/stop`.
- **Cuentas** — en `mode=monitor`, no pausadas.

## 3. Problem statement

El monitor debe descubrir vídeos nuevos sin duplicar requests: respeta el throttle de 30s por cuenta (L-G1). Sin el manejo correcto de `last_check_at=NULL`, las cuentas recién añadidas nunca se comprueban.

## 4. Requirements

#### ADDED: Ciclo del monitor (§4.9)
**After:** `services/monitor.py` con `run_monitor_cycle()` — consulta cuentas en `mode=monitor` y no pausadas; respeta el throttle de 30s por cuenta (L-G1: `last_check_at=NULL` → se comprueba SIEMPRE; `last_check_at < 30s` → se salta); descubre vídeos nuevos (yt-dlp vía to_thread); encola descargas al motor; notifica por descarga si `notify_on_download` (L-G2: canal síncrono).

#### ADDED: Monitor arranca detenido (§5.1/T60)
**After:** El monitor arranca siempre detenido (`MONITOR_AUTOSTART=false`); `monitor start/stop` escriben `monitor_running` en `daemon_state`; el heartbeat lo aplica en caliente. `accounts add` NO arranca el monitor (T60).

#### ADDED: El monitor NO arranca el backfill (§10)
**After:** El monitor solo detecta vídeos nuevos y los encola al motor de descarga. No inicia backfills ni toca `backfill_status`.

#### ADDED: accounts check respeta throttle (T20/L-G1)
**After:** `accounts check` manual también respeta el throttle de 30s (L-G1: NULL se comprueba siempre).

## 5. Solution and main flow

1. `services/monitor.py`: `run_monitor_cycle()`.
2. Helper `_should_check(account, now)` — L-G1 (NULL → True; <30s → False).
3. Comandos `monitor start/stop` (escriben monitor_running).

## 6. Alternative flows / edge cases

- **last_check_at NULL**: se comprueba siempre (L-G1).
- **last_check_at < 30s**: se salta (throttle).
- **Cuenta pausada**: se salta.
- **Cuenta en mode history**: se salta (solo monitor).

## 7. Assumptions

- `MONITOR_INTERVAL_MINUTES` en Settings.
- `monitor_running` en daemon_state (e01s04/e02).

## 8. Constraints

- Throttle L-G1 (NULL siempre; <30s skip).
- Monitor detenido por defecto (T60/§5.1).
- No arranca backfill (§10).

## 9. Dependencies

- e03s01 (services/accounts), e02 (daemon_state, tasks), e01s02 (Settings).

## 10. Interfaces

- `services/monitor.py` → `run_monitor_cycle()`.
- `cli/monitor.py` → start/stop.
- Consumido por el daemon (e02s03 job).

## 11. Test plan

- `tests/monitor/test_monitor.py`: throttle L-G1 (NULL comprueba, <30s skip), pausadas skip, mode history skip, notifica (L-G2).
- `tests/monitor/test_monitor_cli.py`: start/stop.

## 12. Data

- `monitored_accounts` (last_check_at, mode, paused).
- `daemon_state` (monitor_running).

## 13. Security considerations

- Throttle previene sobrecarga (anti-bot).

## 14. Performance

- ~2 peticiones por comprobación; throttle 30s acota volumen.

## 15. Operational concerns

- Monitor detenido por defecto (seguridad).

## 16. Risks

- **NULL tratado como 0s**: L-G1 (test obligatorio).

## 17. Acceptance criteria

- [ ] `run_monitor_cycle()` con throttle L-G1 (NULL → comprueba; <30s → skip).
- [ ] Solo cuentas mode=monitor no pausadas.
- [ ] No arranca backfill (§10).
- [ ] Notifica si notify_on_download (L-G2).
- [ ] `monitor start/stop` escriben monitor_running.
- [ ] Tests en `tests/monitor/` pasan.

## 18. Out of scope

- Motor de descarga real (e04).
- Backfill (e04).
- Contenido del job del daemon (e02 ya registra).

## 19. Risks (detailed)

- **L-G1**: throttle debe distinguir NULL de recién comprobada.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/monitor/` pasa.
- Tasks `status: passing` en `e03s02-tasks.yaml`.

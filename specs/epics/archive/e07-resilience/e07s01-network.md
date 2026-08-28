# Story e07s01 — Probe de red + pausa/reanudación automática

**type:** feat
**risk:** P0
**context:** domain
**BCPs:** 5
**status:** planned

## 1. Business narrative

Ante una caída real de internet, el daemon pausa automáticamente monitor, backfill y descargas, avisa por Telegram y reanuda al recuperar red (§0.13/§9). `NetworkMonitor` gestiona la máquina de estados online/offline con probe HEAD a endpoints neutrales (nunca TikTok).

## 2. Actors

- **Daemon** — ejecuta el probe periódicamente.
- **Motor de descargas** — espera `network_available.wait()` antes de cada intento.
- **Usuario** — recibe notificaciones network.offline/online.

## 3. Problem statement

Sin NetworkMonitor, una caída de red produce fallos masivos (retry_count consumido T64, cookies invalidadas). El monitor debe: detectar caída real (umbral de fallos consecutivos), pausar, notificar, y reanudar con la duración de la caída (T35).

## 4. Requirements

#### ADDED: NetworkMonitor (§9)
**After:** `core/network_monitor.py` con máquina de estados online/offline; probe HEAD con httpx (timeout `NETWORK_PROBE_TIMEOUT_SECONDS` desde Settings, F-13) a endpoints neutrales (`NETWORK_PROBE_URL` lista, nunca TikTok). Umbral `NETWORK_OFFLINE_THRESHOLD_CONSECUTIVE_FAILURES` (default 2) para confirmar caída.

#### ADDED: network_available Event (L-D2)
**After:** `network_available` (asyncio.Event) inyectado en motor/monitor/jobs — **se crea YA SETEADO** (L-D2: sin monitor, la red se asume disponible). El motor espera `network_available.wait()` antes de cada intento.

#### ADDED: Transición de estados con notificación correcta (T35)
**After:** `network.online` se notifica SOLO desde estado `offline` confirmado (un blip no genera "de vuelta online" engañoso); capturar la duración de la caída ANTES de limpiar `offline_since` (T35). `network.offline` una sola vez al confirmar la caída.

#### ADDED: Backoff del probe (F-13)
**After:** Mientras offline, el intervalo crece con backoff (30s → techo 120s con jitter); el job se re-programa tras cada ciclo (F-13).

#### ADDED: Fallos de red no penalizan (T64)
**After:** Fallos de red no incrementan retry_count ni consumen presupuesto (T64); no invalidan cookies.

#### ADDED: Drenaje del spool (T42)
**After:** En transición a `online` confirmado se drena `pending_notifications` en orden de created_at (T42).

## 5. Solution and main flow

1. `core/network_monitor.py`: NetworkMonitor con probe + estados + backoff.
2. Evento `network_available` (L-D2).
3. Transición T35 (duración capturada antes de limpiar).
4. Drenaje del spool (T42).

## 6. Alternative flows / edge cases

- **Blip de red**: no confirma offline → sin notificación online (T35).
- **Timeout probe**: cuenta como fallo; umbral 2.

## 7. Assumptions

- httpx instalado (e01s01); NETWORK_PROBE_* en Settings.

## 8. Constraints

- Probe nunca a TikTok (§1).
- Evento por defecto seteado (L-D2).
- network.online solo desde offline confirmado (T35).
- Red no penaliza (T64).

## 9. Dependencies

- e01s02 (Settings), e01s03 (logging), e02 (daemon), e06 (notif).

## 10. Interfaces

- `core/network_monitor.py` → NetworkMonitor, network_available.
- Consumido por motor (e04), daemon (e02s03).

## 11. Test plan

- `tests/network/test_probe.py`: probe HEAD, umbral, backoff (F-13).
- `tests/network/test_pause_resume.py`: transición T35, L-D2, T64.

## 12. Data

- Ninguno (estado en memoria; notif. vía spool).

## 13. Security considerations

- Probe a endpoints neutrales (nunca TikTok).

## 14. Performance

- Backoff evita insistencia agresiva.

## 15. Operational concerns

- network.offline/online notificados.

## 16. Risks

- **Blip notificado como online**: T35 (test).

## 17. Acceptance criteria

- [ ] NetworkMonitor con estados online/offline.
- [ ] Probe HEAD neutral (F-13 timeout desde Settings); umbral 2 fallos.
- [ ] network_available seteado por defecto (L-D2).
- [ ] network.online solo desde offline confirmado + duración (T35).
- [ ] Backoff 30→120s con jitter (F-13).
- [ ] Red no penaliza (T64); drenaje spool (T42).
- [ ] Tests en `tests/network/` pasan.

## 18. Out of scope

- Circuit breaker (e07s03).
- Disco lleno (e07s02).

## 19. Risks (detailed)

- **T35**: duración capturada antes de limpiar.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/network/` pasa.
- Tasks `status: passing` en `e07s01-tasks.yaml`.

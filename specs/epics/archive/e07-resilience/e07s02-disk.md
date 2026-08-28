# Story e07s02 — Disco lleno (downloads_paused + system disk)

**type:** feat
**risk:** P0
**context:** domain
**BCPs:** 4
**status:** planned

## 1. Business narrative

Ante disco lleno (ENOSPC), las rutas de descarga se pausan globalmente (`downloads_paused=1`), se alerta por Telegram, y se reanudan automáticamente al recuperar espacio (job de disco, T45/T65). `system disk --resume` fuerza la reanudación manual.

## 2. Actors

- **Motor** — consulta `downloads_paused` + `network_available` antes de cada intento.
- **Job de disco** — productor de `monitor.disk_warning` + reanudación (T65).
- **Usuario** — `system disk [--resume]`.

## 3. Problem statement

El ENOSPC debe ser un fallo local accionable (no transitorio de TikTok): pausar descargas, alertar, y reanudar al recuperar espacio — sin contar para el breaker ni tocar cookies (T45).

## 4. Requirements

#### ADDED: ENOSPC → downloads_paused (T45)
**After:** `core/disk.py` (o services/system): detecta ENOSPC → `downloads_paused=1` en daemon_state (helper T37) + alerta Telegram. NO cuenta para breaker ni toca cookies (T45).

#### ADDED: El motor consulta downloads_paused (§4.4 punto 6)
**After:** El motor consulta `downloads_paused` junto a `network_available.wait()` antes de cada intento.

#### ADDED: Job de disco (T65)
**After:** Job (15-30 min, umbral `DISK_WARNING_FREE_PERCENT`) — productor de `monitor.disk_warning`; detecta espacio libre de nuevo → reanudación automática (`downloads_paused=0`) con notificación (T65).

#### ADDED: system disk (§3)
**After:** `system disk` muestra uso, alertas y estado de `downloads_paused`; `--resume` fuerza reanudación manual limpiando el flag.

## 5. Solution and main flow

1. `core/disk.py`: check_disk_usage, set_downloads_paused (T37).
2. `services/system.py` (o en disk): job de disco (T65) + reanudación.
3. `cli/system.py`: system disk [--resume].

## 6. Alternative flows / edge cases

- **Espacio bajo umbral**: monitor.disk_warning (T65).
- **ENOSPC**: downloads_paused=1 (T45).
- **Espacio libre de nuevo**: reanudación automática.

## 7. Assumptions

- `downloads_paused` en daemon_state (e01s04).

## 8. Constraints

- ENOSPC no cuenta para breaker ni cookies (T45).
- Tests con disk_usage mockeado (T69).

## 9. Dependencies

- e02s04 (daemon_state helpers), e01s02 (Settings).

## 10. Interfaces

- `core/disk.py` → check_disk_usage, set_downloads_paused.
- `cli/system.py` → system disk.

## 11. Test plan

- `tests/disk/test_disk_full.py`: ENOSPC → paused (T45), job warning (T65), reanudación, --resume.

## 12. Data

- `daemon_state.downloads_paused`.

## 13. Security considerations

- Sin secretos.

## 14. Performance

- Job 15-30 min; pausa evita reintentos inútiles.

## 15. Operational concerns

- system disk --resume manual.

## 16. Risks

- **Test con disco real**: T69 (mockear).

## 17. Acceptance criteria

- [ ] ENOSPC → downloads_paused=1 + alerta (T45).
- [ ] Motor consulta downloads_paused + network_available (§4.4).
- [ ] Job de disco productor de disk_warning + reanudación (T65).
- [ ] system disk [--resume] (§3).
- [ ] Tests con disk_usage mockeado (T69).

## 18. Out of scope

- Circuit breaker (e07s03).
- Backup (e09).

## 19. Risks (detailed)

- **T69**: mockear shutil.disk_usage.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/disk/` pasa.
- Tasks `status: passing` en `e07s02-tasks.yaml`.

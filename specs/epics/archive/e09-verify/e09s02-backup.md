# Story e09s02 — system backup (snapshot VACUUM INTO + retención)

**type:** feat
**risk:** P1
**context:** domain
**BCPs:** 8
**status:** planned

## 1. Business narrative

`system backup` crea un snapshot consistente en caliente de la DB con `VACUUM INTO` en `<DATA_DIR>/backups/` (nunca copiar el .db a pelo bajo WAL, F-21b). Purga los snapshots más antiguos por encima de `SYSTEM_BACKUP_RETAIN_COUNT` (default 7, §23.3.6). Procedimiento de restauración documentado en README.

## 2. Actors

- **Usuario** — `system backup`.
- **Operador** — restaura el snapshot (daemon detenido).

## 3. Problem statement

Sin backup, la DB (cuentas, cursor, cookies cifradas) es un punto único de fallo. Copiar el .db a pelo bajo WAL captura un estado inconsistente — `VACUUM INTO` es la API segura (F-21b).

## 4. Requirements

#### ADDED: system backup con VACUUM INTO (§3/F-21b)
**After:** `services/backup.py` — `create_backup(data_dir)` con `VACUUM INTO '<DATA_DIR>/backups/tikdown-rs-<fecha>.db'` (nunca copiar .db bajo WAL). Mapea `sqlite3.OperationalError` a error limpio.

#### ADDED: Retención (§23.3.6)
**After:** Purga los snapshots más antiguos por encima de `SYSTEM_BACKUP_RETAIN_COUNT` (default 7). La purga es best-effort (T14).

#### ADDED: CLI system backup (§3)
**After:** `system backup` en el grupo system (ya existe cli/system.py).

#### ADDED: Restauración documentada (§23.3.6)
**After:** README documenta: con el daemon DETENIDO, copiar el snapshot sobre el .db, eliminar WAL/SHM.

## 5. Solution and main flow

1. `services/backup.py`: create_backup (VACUUM INTO) + purge (retención).
2. `cli/system.py`: system backup.
3. README: procedimiento de restauración.

## 6. Alternative flows / edge cases

- **DB en uso**: VACUUM INTO es seguro en caliente.
- **OperationalError**: error limpio.
- **Purga falla**: best-effort (T14).

## 7. Assumptions

- sqlite3 stdlib con VACUUM INTO (SQLite ≥ 3.27).

## 8. Constraints

- Nunca copiar .db bajo WAL (F-21b).
- Retención default 7 (§23.3.6).
- Purga best-effort (T14).

## 9. Dependencies

- e01s02 (Settings data_dir), e08s01 (cli).

## 10. Interfaces

- `services/backup.py` → create_backup.
- `cli/system.py` → system backup.

## 11. Test plan

- `tests/backup/test_backup.py`: VACUUM INTO crea snapshot, retención purga, error limpio.

## 12. Data

- `<DATA_DIR>/backups/`.

## 13. Security considerations

- Snapshot contiene cookies cifradas — mismo cuidado que la DB.

## 14. Performance

- VACUUM INTO en caliente.

## 15. Operational concerns

- Restauración: daemon detenido + copiar + eliminar WAL/SHM.

## 16. Risks

- **Snapshot inconsistente**: VACUUM INTO (F-21b).

## 17. Acceptance criteria

- [ ] create_backup con VACUUM INTO (F-21b).
- [ ] Retención SYSTEM_BACKUP_RETAIN_COUNT (7) con purga best-effort (T14).
- [ ] system backup en cli (§3).
- [ ] Restauración documentada en README (§23.3.6).
- [ ] Error limpio ante OperationalError.
- [ ] Tests en `tests/backup/` pasan.

## 18. Out of scope

- Restauración automática (manual).

## 19. Risks (detailed)

- **F-21b**: nunca copiar .db bajo WAL.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/backup/` pasa.
- Tasks `status: passing` en `e09s02-tasks.yaml`.

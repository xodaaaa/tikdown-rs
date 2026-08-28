# Story e01s04 — Modelos SQLAlchemy + migración Alembic + WAL

**type:** feat
**risk:** P0
**context:** infra
**BCPs:** 8
**status:** planned

## 1. Business narrative

TikDown-rs necesita el modelo de datos completo (§2) y la infraestructura de base de datos SQLite (WAL) con migraciones Alembic idempotentes. Es la base de datos de coordinación CLI↔daemon: sin ella, ninguna story posterior (cuentas, backfill, cookies) tiene dónde persistir estado.

## 2. Actors

- **Daemon / CLI** — procesos que abren la misma DB SQLite (WAL) vía SQLAlchemy async.
- **Alembic** — migraciones de esquema.
- **Operador** — inspección con `sqlite3`.

## 3. Problem statement

La coordinación entre procesos (daemon + CLI) depende de SQLite en WAL. El esquema debe soportar todas las tablas de §2 con sus CHECK constraints, y las migraciones deben ser idempotentes y seguras bajo concurrencia (T17, T29, T68) y portables (T51, T70).

## 4. Requirements

#### ADDED: Modelos SQLAlchemy async (§2)
**After:** `models/models.py` define: `MonitoredAccount` (mode, paused, needs_review, notify_on_download, monitor_after_backfill, backfill_status con CHECK incluyendo 'cancelled' desde el primer esquema L-F7, backfill_cursor/total/done, last_check_at, counts), `Video` (tiktok_video_id UNIQUE, account_id FK, url/title/description/duration, upload_date YYYYMMDD, local_path absoluto T8, file_size, file_hash, status downloaded|failed|cancelled|skipped, retry_count, error_message, error_category definitive|transient|integrity), `Cookie` (encrypted_blob LargeBinary, validation_state valid|invalid|inconclusive, last_validated_at, last_validation_reason), `DaemonState` (singleton CHECK id=1), `DownloadPacingState` (singleton, next_allowed_at ms), `DownloadArchive`, `PendingNotification`. Índices recomendados (§2).

#### ADDED: db.py — WAL, PRAGMAs, NullPool, listener
**After:** `core/db.py` con `create_async_engine` y `poolclass=NullPool` (excepto tests: StaticPool). PRAGMAs en evento `connect` con **orden obligatorio**: `busy_timeout` PRIMERO, `journal_mode=WAL` DESPUÉS (L-C5). Crea el directorio padre de la DB si no existe (L-C9, chequeo estructural `"///" not in url`). Listener `handle_error` para `sqlite3.OperationalError: database is locked` → contador + log `db.busy_timeout` (§5.8).

#### ADDED: Migración Alembic inicial idempotente (T29/T51/T68/T70)
**After:** Migración inicial con todas las tablas + CHECK constraints. `alembic/env.py` async (`connection.run_sync`, T51). `core/migrations.py` con: comprobar `alembic_version` antes de decidir `stamp` vs `upgrade` (T29), lock de fichero `<DATA_DIR>/.migrate.lock` (T68), localización de `alembic.ini`/`alembic/` por candidatos (T70: junto al módulo, luego cwd; si ninguno, `FileNotFoundError` accionable), logger alembic a WARNING.

#### ADDED: Singleton idempotente (T17)
**After:** `daemon_state` y `download_pacing_state` con `INSERT ... ON CONFLICT DO NOTHING` + **commit inmediato** (L-C6) + relectura. La migración NO inserta la fila (T17).

## 5. Solution and main flow

1. `models/models.py` — modelos SQLAlchemy async.
2. `core/db.py` — engine WAL + PRAGMAs (L-C5) + NullPool + directorio padre (L-C9) + listener.
3. Migración Alembic inicial + `env.py` async (T51).
4. `core/migrations.py` — idempotente (T29) + lock (T68) + candidatos (T70).

## 6. Alternative flows / edge cases

- **Concurrencia de arranque**: singletons con ON CONFLICT (T17) + lock de migración (T68).
- **Instalación wheel**: `alembic.ini` resuelto por candidatos (T70).
- **DB en directorio nuevo**: crear padre (L-C9).

## 7. Assumptions

- SQLAlchemy 2.0.52 + aiosqlite 0.22.1 instalados (verificado en e01s01).
- WAL activado; coordinación multi-proceso real.

## 8. Constraints

- PRAGMA order: busy_timeout PRIMERO, journal_mode DESPUÉS (L-C5).
- NullPool en engine; StaticPool solo en tests `:memory:`.
- Migraciones idempotentes desde el día uno (T29/T68/T70/T51).

## 9. Dependencies

- e01s01 (toolchain), e01s02 (Settings data_dir), e01s03 (logging).

## 10. Interfaces

- `models/models.py` → modelos.
- `core/db.py` → engine/session.
- `core/migrations.py` → apply_migrations().
- Consumido por daemon (e02), servicios (e03+).

## 11. Test plan

- `tests/models/test_models.py`: columnas, índices, CHECK constraints.
- `tests/models/test_schema.py`: singleton idempotente (T17), migraciones (T29/T68/T70), PRAGMA order (L-C5).
- `tests/db/test_db.py` (si aplica): listener, directorio padre.

## 12. Data

Esquema completo de §2.

## 13. Security considerations

- `encrypted_blob` como LargeBinary (bytes, no Text — ciphertext Fernet).
- Sin secretos en modelos.

## 14. Performance

- WAL para lectura/escritura concurrente; NullPool evita problemas de threads.

## 15. Operational concerns

- Migraciones idempotentes en cada arranque/CLI (excepto healthcheck/--version, R10).

## 16. Risks

- **Carreras de migración**: lock T68.
- **PRAGMA order**: busy_timeout primero (L-C5).

## 17. Acceptance criteria

- [ ] Modelos de §2 con CHECK constraints (incl. 'cancelled' en backfill_status).
- [ ] `core/db.py` con WAL, PRAGMA order (L-C5), NullPool, directorio padre (L-C9), listener.
- [ ] Migración Alembic inicial aplica; `env.py` async (T51).
- [ ] `core/migrations.py` idempotente (T29), lock (T68), candidatos (T70).
- [ ] Singletons con ON CONFLICT + commit (T17, L-C6).
- [ ] Tests en `tests/models/` pasan.

## 18. Out of scope

- Uso de modelos por servicios (e03+).
- Creación de singletons helpers (T37) — e02.

## 19. Risks (detailed)

- **Pérdida de fila singleton sin commit**: L-C6 (commit inmediato).
- **alembic.ini no encontrado en wheel**: T70 (candidatos).

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/models/ -q` y `uv run alembic upgrade head` pasan.
- Tasks `status: passing` en `e01s04-tasks.yaml`.

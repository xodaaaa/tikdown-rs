# Story e14s01: RotatingFileHandler/TimedRotatingFileHandler JSON + config env (T72)

## 1. Metadata

| Campo | Valor |
|-------|-------|
| story_id | e14s01 |
| epic | e14-logs-rotados |
| type | feat |
| risk | P2 |
| context | infra |
| bcps | 4 |
| delta | ADDED |

## 2. Título

Logs a archivo rotado: JSON por tamaño (10MB) o tiempo (diario), con retención configurable.

## 3. Problema

El daemon emite JSON a stdout (principio 8, §1). Sin persistencia a archivo rotado, los logs se
pierden al rotar el contenedor y no hay retención histórica. Se quiere logs a archivo rotado
(manteniendo JSON) además de stdout, configurable por entorno.

## 4. Contexto

`core/logging.py` tiene `setup_logging(level, json_output)` con `JsonFormatter` y `basicConfig(force=True)`
(reaplicable tras migración Alembic, T72). `Settings` (core/config.py) no tiene campos de archivo de log.

## 5. Alcance

- Campos de Settings: `LOG_FILE_PATH`, `LOG_FILE_MAX_BYTES` (10MB), `LOG_FILE_BACKUP_COUNT` (7), `LOG_FILE_WHEN`.
- Handler de archivo rotado en `setup_logging` (RotatingFileHandler o TimedRotatingFileHandler), JSON.
- `run.py` pasa los campos desde Settings; T72 respetado (reaplicación usa la misma config).

## 6. Fuera de alcance

- Rotación por compresión (gzip) de backups.
- Logging a archivo SOLO (sin stdout) — se mantiene stdout + archivo.
- Nivel de log por archivo distinto al stdout.

## 7. Stack y dependencias

- `logging.handlers.RotatingFileHandler` / `TimedRotatingFileHandler` (stdlib).
- `JsonFormatter` existente.
- **Sin dependencias nuevas.**

## 8. Diseño

```
Settings:
  log_file_path ("" = solo stdout)
  log_file_max_bytes (10*1024*1024)
  log_file_backup_count (7)
  log_file_when ("size" | "midnight")

setup_logging(level, json_output, log_file_path="", max_bytes=10MB, backup_count=7, when="size"):
  handlers = [StreamHandler(stdout, JsonFormatter)]
  if log_file_path:
    if when == "size":
      handler = RotatingFileHandler(path, maxBytes, backupCount)
    else:
      handler = TimedRotatingFileHandler(path, when="midnight", backupCount)
    handler.setFormatter(JsonFormatter())
    handlers.append(handler)
  basicConfig(level, handlers, force=True)   # T72: reaplicable
```

## 9. Requisitos

### ADDED: Config de archivo de log
**After:** `Settings` expone `LOG_FILE_PATH`, `LOG_FILE_MAX_BYTES` (10MB), `LOG_FILE_BACKUP_COUNT`
(7), `LOG_FILE_WHEN` ("size" | "midnight"). Vacío → solo stdout.

### ADDED: Handler de archivo rotado
**After:** `setup_logging` añade `RotatingFileHandler` (por tamaño) o `TimedRotatingFileHandler`
(por tiempo) con el mismo `JsonFormatter`; directorio padre creado; stdout se mantiene.

### ADDED: T72 respetado
**After:** La reaplicación tras migraciones usa la misma config (campos de Settings pasados a
`setup_logging`).

## 10. Comportamiento

1. Sin `LOG_FILE_PATH` → solo stdout (comportamiento actual).
2. Con path → líneas JSON al archivo + stdout.
3. `LOG_FILE_WHEN=size` → rotación por tamaño (10MB), backups `.1`, `.2`...
4. `LOG_FILE_WHEN=midnight` → rotación diaria.
5. Tras migración Alembic → la reaplicación respeta el archivo (T72).

## 11. Pasos de implementación

1. Campos en Settings → verify: `uv run pytest tests/logging/test_logging_rotated.py -q`
2. Handler rotado en setup_logging → verify: `uv run pytest tests/logging/test_logging_rotated.py -q`
3. run.py pasa campos; T72 → verify: `uv run pytest tests/logging/test_logging_rotated.py -q`
4. Tests F.I.R.S.T. → verify: `uv run pytest tests/logging/ -q`

## 12. Script de verificación (step-by-step)

1. `uv run pytest tests/logging/test_logging_rotated.py -q` → tests pasan.
2. `uv run pytest tests/logging/ -q` → todos los tests de logging pasan.
3. `uv run pytest --cov=tikdown_rs --cov-report=term -q` → suite completa sin regresión.
4. `uv run ruff check . && uv run ruff format --check .` → lint OK.
5. `uv run tikdown-rs --version` → CLI intacta (smoke).

## 13. Criterios de aceptación

- [ ] Settings con los 4 campos LOG_FILE_*.
- [ ] `setup_logging` escribe JSON al archivo rotado + stdout.
- [ ] Rotación por tamaño (10MB) y por tiempo (midnight) funcionan.
- [ ] backup_count aplicado.
- [ ] T72: reaplicación respeta el archivo.
- [ ] Sin dependencias nuevas.

## 14. Definición de éxito

`tests/logging/` pasa, el archivo recibe JSON, la rotación por tamaño crea backups, sin regresión.

## 15. Saliendo

- Rama `feat/e14-logs-rotados` vía kickoff-branch.
- Commits Conventional Commits separados RED/GREEN.

## 16. Riesgos

| Riesgo | Detección |
|--------|-----------|
| Rotación por tamaño no dispara | Test con maxBytes pequeño |
| TimedRotatingFileHandler no se usa | Test con when=midnight |
| El archivo no recibe JSON | Test de contenido |
| T72 roto (pierde archivo tras migración) | Test de reaplicación |

## 17. Criterios de aceptación (checklist)

- [ ] Story spec en `specs/epics/e14-logs-rotados/e14s01-logs-rotados.md`
- [ ] Tasks con `status: failing` (no pre-marcados)
- [ ] Tests en `tests/logging/test_logging_rotated.py`

## 18. Seguimiento

- Estado: `failing` → `passing` tras verify-work.
- `state.yaml` `active_flow` → `build_epic`.

## 19. Notas

- §1: JSON a stdout (principio 8) se MANTIENE; el archivo es adicional.
- T72: reaplicar logging tras migraciones — la config del archivo viene de Settings, se respeta.
- RotatingFileHandler por tamaño; TimedRotatingFileHandler por tiempo.

## 20. Riesgo (técnico)

P2 — infraestructura de logging; sin lógica de negocio. Rotación y retención son la pieza a verificar.

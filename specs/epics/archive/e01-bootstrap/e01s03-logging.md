# Story e01s03 — Logging JSON ad-hoc (stdlib)

**type:** feat
**risk:** P1
**context:** infra
**BCPs:** 2
**status:** planned

## 1. Business narrative

TikDown-rs necesita logging estructurado como fuente de verdad local. El plan exige **logging stdlib con formatter JSON ad-hoc** (decisión F-20: `structlog` se retiró del stack por no tener consumidor). JSON a stdout en el daemon; consola legible en CLI. Nivel vía `LOG_LEVEL`. Además, **T72**: la migración Alembic (`fileConfig`) pisa el root logger — el setup de logging debe reaplicarse tras migrar.

## 2. Actors

- **Daemon** — emite logs JSON a stdout (consumido por Docker/journald).
- **CLI** — emite logs legibles en consola.
- **Operador** — lee `docker logs` o la consola; nivel vía `LOG_LEVEL`.

## 3. Problem statement

Sin logging estructurado, el daemon no tiene fuente de verdad local: los logs no son parseables (JSON) ni el nivel es configurable. Además, sin manejar T72, un daemon que migra pierde silenciosamente todos sus logs INFO tras la primera migración (docker logs con 0 bytes, L-J3).

## 4. Requirements

#### ADDED: Formatter JSON propio sobre logging stdlib
**After:** `core/logging.py` define un `JsonFormatter` (ad-hoc, stdlib) que emite cada registro como JSON a stdout con campos: timestamp, level, logger, message, y `extra` opcional. Nunca structlog (F-20).

#### ADDED: Setup de logging por destino (daemon/CLI)
**After:** `_setup_logging(level)` configura el root logger: JSON a stdout en el daemon; consola legible en CLI. Nivel vía `LOG_LEVEL` desde Settings. Logger del proyecto `tikdown_rs.*`.

#### ADDED: Reaplicable tras migración (T72)
**After:** El setup usa `basicConfig(force=True)` para que, tras la migración Alembic (`fileConfig` pisa el root logger), pueda reaplicarse y restaurar el formatter JSON + nivel. `disable_existing_loggers=False` no basta (T72).

## 5. Solution and main flow

1. Definir `JsonFormatter` en `core/logging.py`.
2. Implementar `setup_logging(level, json_output: bool)` (daemon=JSON, CLI=consola).
3. Exponer función reaplicable con `force=True` (para T72).

## 6. Alternative flows / edge cases

- **Migración Alembic pisa root logger**: reaplicar `setup_logging` tras migrar (T72).
- **Nivel inválido**: logging stdlib lanza ValueError → validar en Settings (e01s02).

## 7. Assumptions

- logging stdlib + formatter JSON (F-20); sin structlog.
- `LOG_LEVEL` desde Settings.

## 8. Constraints

- Nunca structlog ni otra dependencia de logging (F-20).
- JSON a stdout en daemon; consola legible en CLI.
- Toda reaplicación con `force=True`.

## 9. Dependencies

- e01s02 (Settings con `log_level`).

## 10. Interfaces

- `core/logging.py` → `JsonFormatter`, `setup_logging(level, json_output)`.
- Consumido por daemon runner (e02) y CLI common (e08).

## 11. Test plan

- `tests/logging/test_logging.py`: el formatter emite JSON válido con campos esperados; setup aplica LOG_LEVEL; reaplicable tras migración (T72).

## 12. Data

Ninguno.

## 13. Security considerations

- Logs JSON sin datos sensibles (no loguear tokens/cookies).

## 14. Performance

- Formatter JSON ad-hoc (sin dependencias pesadas).

## 15. Operational concerns

- `LOG_LEVEL` controla verbosidad; JSON parseable por journald.

## 16. Risks

- `basicConfig(force=True)` puede resetear handlers existentes → usarlo solo en reaplicación T72.

## 17. Acceptance criteria

- [ ] `core/logging.py` define `JsonFormatter` (stdlib) que emite JSON válido.
- [ ] `setup_logging(level, json_output)` configura JSON (daemon) o consola (CLI).
- [ ] `basicConfig(force=True)` permite reaplicación tras migración (T72).
- [ ] Tests en `tests/logging/test_logging.py` pasan.
- [ ] Sin structlog (F-20).

## 18. Out of scope

- Consumo por daemon runner (e02) / CLI common (e08).
- Migración Alembic real (e01s04).

## 19. Risks (detailed)

- **Pérdida de logs tras migración**: mitigado con reaplicación `force=True` (T72).

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/logging/test_logging.py` pasa.
- Tasks `status: passing` en `e01s03-tasks.yaml`.

# Story e08s01 — Estructura typer + 7 grupos + cli/common.py

**type:** feat
**risk:** P1
**context:** infra
**BCPs:** 8
**status:** planned

## 1. Business narrative

La CLI se organiza en **exactamente 7 grupos de sustantivo** (§3). `cli/main.py` registra los grupos con `app.add_typer(..., name="sustantivo")` + `@app.callback` global `--version` (L-A1). `cli/common.py` centraliza los wrappers `asyncio.run()` (T18), `run_or_exit()` (F-21) y las migraciones + Settings por invocación (§5.5/§5.6).

## 2. Actors

- **Usuario** — `tikdown-rs <grupo> <verbo>`.
- **Docker** — entrypoint `tikdown-rs daemon run`.

## 3. Problem statement

Sin el callback global (L-A1), `--help` revienta. Sin wrappers centralizados (T18), cada comando duplica asyncio.run. Sin migraciones por invocación (§5.5), el esquema se queda atrás en comandos de negocio.

## 4. Requirements

#### ADDED: 7 grupos + callback global (L-A1)
**After:** `cli/main.py` — `@app.callback()` con `--version` e `invoke_without_command=True` (L-A1); `app.add_typer(daemon, monitor, accounts, backfill, cookies, videos, system)` — exactamente 7 grupos, sin comandos sueltos en la raíz (§3).

#### ADDED: cli/common.py (T18/F-21)
**After:** `cli/common.py` — `run_sync(coro)` (wrapper asyncio.run, T18); `run_or_exit(fn)` convierte `AccountError`/`BackfillAccountError`/`ConfigurationError` en `ERROR <mensaje>` + exit 1 sin tracebacks (F-21); `prepare_invocation()` aplica migraciones idempotentes (§5.5, T29/T68/T70) + Settings fresca (§5.6). `--version` y healthcheck NO migran (R10).

## 5. Solution and main flow

1. `cli/common.py`: run_sync, run_or_exit, prepare_invocation.
2. `cli/main.py`: app con callback + 7 grupos.

## 6. Alternative flows / edge cases

- **--help sin subcomando**: callback global (L-A1).
- **--version**: no migra (R10).
- **Error de negocio**: ERROR + exit 1 (F-21).

## 7. Assumptions

- Grupos ya existen (daemon/accounts/monitor/backfill/cookies/system de e02-e07); falta videos (e09) — registrar el que exista.

## 8. Constraints

- 7 grupos de sustantivo (§3).
- Wrappers centralizados (T18).
- Migraciones por invocación (T29/T68/T70); --version/healthcheck no migran (R10).

## 9. Dependencies

- Grupos CLI de e02-e07.

## 10. Interfaces

- `cli/main.py` → app.
- `cli/common.py` → run_sync, run_or_exit.

## 11. Test plan

- `tests/cli/test_common.py`: run_or_exit (F-21), prepare_invocation (migraciones).

## 12. Data

- Ninguno directo.

## 13. Security considerations

- Sin secretos en salida.

## 14. Performance

- Migraciones solo si pendientes (T29).

## 15. Operational concerns

- Error limpio sin traceback (F-21).

## 16. Risks

- **L-A1**: callback global obligatorio.

## 17. Acceptance criteria

- [ ] app con callback --version + invoke_without_command (L-A1).
- [ ] 7 grupos registrados (§3); sin comandos sueltos.
- [ ] run_sync (T18) + run_or_exit (F-21).
- [ ] prepare_invocation: migraciones (T29/T68/T70) + Settings.
- [ ] --version no migra (R10).
- [ ] Tests en `tests/cli/` pasan.

## 18. Out of scope

- Salida rich/--json (e08s02).
- videos group (e09).

## 19. Risks (detailed)

- **L-A1**: callback obligatorio.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/cli/` pasa.
- Tasks `status: passing` en `e08s01-tasks.yaml`.

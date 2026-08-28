# Story e08s02 — Salida rich + --json + videos export (CSV sanitizado)

**type:** feat
**risk:** P1
**context:** infra
**BCPs:** 6
**status:** planned

## 1. Business narrative

Salida CLI rica (rich) para humanos + `--json` para scripting/bot (§3). Marcadores ASCII puros (L-A5). Exportación de vídeos (json/csv) sin wrap ni markup de Rich (L-A6) con sanitización CSV anti-inyección (T49). Barras de progreso con nombres no colisionantes (T3).

## 2. Actors

- **Usuario** — `videos last/export`, comandos con --json.
- **Bot** — reutiliza la salida --json.
- **Scripts** — consumen --json.

## 3. Problem statement

Sin --json, el bot no puede reutilizar la CLI. Los glifos Unicode revientan en Windows (L-A5). El export Rich corrompe JSON/CSV (L-A6). El CSV sin sanitizar permite inyección de fórmulas (T49).

## 4. Requirements

#### ADDED: Salida rich + --json (§3)
**After:** Todas las salidas soportan `--json` (para scripting/bot). Salida humana con rich (tablas, paneles). Marcadores **ASCII puros** (OK/ERROR, `-`, `!`) — NUNCA glifos Unicode (L-A5).

#### ADDED: Barras de progreso sin colisión (T3)
**After:** `rich.progress.Progress` con campos propios `{task.fields[clave]}` (corchetes, T3); nombres no colisionantes (`procesados`, `correctos`, `fallidos`, `esperados` — nunca total/completed); test de render con datos simulados.

#### ADDED: videos last / export / integrity (§3)
**After:** `videos last [N]`, `videos export [--format json|csv]`, `videos integrity [username]`. Export con `console.print(markup=False, soft_wrap=True)` (L-A6).

#### ADDED: CSV sanitizado (T49)
**After:** `csv.writer` stdlib (quoting RFC 4180) + sanitización `lstrip(" \t\r\n\x0b\x0c")` antes del chequeo de operadores `= + - @` (T49/F-11, OWASP/CWE-1236).

## 5. Solution and main flow

1. `cli/output.py` (o en common): helpers de salida (rich/--json, ASCII).
2. `cli/videos.py`: last/export/integrity.
3. `services/export.py`: export json/csv con sanitización.

## 6. Alternative flows / edge cases

- **CSV con = en el dato**: lstrip + prefijo seguro.
- **No-TTY**: export sin wrap (L-A6).

## 7. Assumptions

- rich instalado (e01s01); modelos videos (e01s04).

## 8. Constraints

- ASCII puro (L-A5).
- Export sin markup/wrap (L-A6).
- CSV sanitizado (T49).

## 9. Dependencies

- e01s04 (modelos), e08s01 (common).

## 10. Interfaces

- `cli/videos.py` → last/export/integrity.
- `services/export.py` → export_csv/export_json.

## 11. Test plan

- `tests/cli/test_output.py`: ASCII (L-A5), --json, T3 (render barra), T49 (CSV).
- `tests/export/test_export.py`: sanitización CSV.

## 12. Data

- `videos`.

## 13. Security considerations

- CSV anti-inyección (T49).

## 14. Performance

- N/A.

## 15. Operational concerns

- --json para scripting.

## 16. Risks

- **Glifos Unicode**: L-A5 (test).
- **Inyección CSV**: T49.

## 17. Acceptance criteria

- [ ] Salidas con --json (§3).
- [ ] ASCII puro (L-A5); export sin wrap (L-A6).
- [ ] Barras T3 (fields[clave], nombres no colisionantes, render test).
- [ ] videos last/export/integrity (§3).
- [ ] CSV sanitizado (T49).
- [ ] Tests en `tests/cli/` y `tests/export/` pasan.

## 18. Out of scope

- system backup (e09).

## 19. Risks (detailed)

- **T49**: sanitización CSV.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/cli/ tests/export/` pasa.
- Tasks `status: passing` en `e08s02-tasks.yaml`.

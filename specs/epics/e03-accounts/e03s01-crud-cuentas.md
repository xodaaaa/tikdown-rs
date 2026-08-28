# Story e03s01 — CRUD de cuentas (services/accounts)

**type:** feat
**risk:** P1
**context:** domain
**BCPs:** 6
**status:** planned

## 1. Business narrative

Gestión de cuentas de TikTok a archivar: añadir, listar, pausar/reactivar, notificar, eliminar, comprobar y ver estadísticas. La lógica vive en `services/accounts` (independiente de cli/daemon, principio §0.5); los comandos CLI del grupo `accounts` solo orquestan (regla de oro §3).

## 2. Actors

- **Usuario** — usa la CLI `tikdown-rs accounts <verbo>`.
- **Bot de Telegram** — reutiliza `services/accounts` (paridad funcional).

## 3. Problem statement

Sin la capa de cuentas, no hay forma de gestionar qué cuentas archivar. Los comandos siguen la organización de §3 (7 grupos de sustantivo, pares simétricos, `remove` no `delete`). El modelo MonitoredAccount (§2) ya existe; la capa de servicios la usa.

## 4. Requirements

#### ADDED: services/accounts add (T60)
**After:** `add(username, mode='history', then_monitor=False)` — username sin `@`, `--mode history|monitor`, `--then-monitor` (solo con history). **No arranca el monitor global** (T60: solo cambia el mode; el monitor sigue detenido hasta `monitor start`).

#### ADDED: services/accounts list / pause / resume / remove / stats
**After:** `list()` con estado y conteos; `pause(username)` / `resume(username)` (par simétrico); `remove(username)` (con confirmación en CLI); `stats(username)`.

#### ADDED: services/accounts notify (L-G3)
**After:** `set_notify(username, on: bool)` — activa/desactiva `notify_on_download`. Se propaga en TODAS las rutas de descarga (L-G3).

#### ADDED: services/accounts check (T20)
**After:** `check(username)` — fuerza comprobación manual **con motor y clave REALES** (T20, nunca simulados), respetando el throttle de 30s (`last_check_at`).

## 5. Solution and main flow

1. `services/accounts.py` — capa de negocio (add/list/pause/resume/remove/stats/notify/check).
2. `cli/accounts.py` — comandos del grupo accounts orquestando services (rich, --json, ASCII puro L-A5).

## 6. Alternative flows / edge cases

- **Username con @**: normalizar `lstrip('@')`.
- **Cuenta duplicada**: error de integridad manejado.
- **check throttle**: respeta `last_check_at` 30s.

## 7. Assumptions

- Modelo MonitoredAccount (e01s04) disponible.

## 8. Constraints

- Capa services independiente de cli/daemon (principio §0.5).
- CLI solo orquesta (regla de oro §3).
- Pares simétricos; `remove` no `delete` (§3).

## 9. Dependencies

- e01s04 (modelos), e02s04 (daemon_state/helpers).

## 10. Interfaces

- `services/accounts.py` → funciones de negocio.
- `cli/accounts.py` → comandos.

## 11. Test plan

- `tests/accounts/test_accounts.py`: add/list/pause/resume/remove/stats/notify/check (T20).
- `tests/cli/test_accounts_cli.py`: orquestación.

## 12. Data

- Tabla `monitored_accounts`.

## 13. Security considerations

- Sin secretos; username normalizado.

## 14. Performance

- N/A (operaciones CRUD simples).

## 15. Operational concerns

- Confirmación en remove.

## 16. Risks

- **check con motor simulado**: T20 (motor real).

## 17. Acceptance criteria

- [ ] `services/accounts.py` con add/list/pause/resume/remove/stats/notify/check.
- [ ] `cli/accounts.py` con los comandos de §3 (add, list, pause, resume, remove, stats, notify, check).
- [ ] add no arranca monitor global (T60).
- [ ] check con motor real (T20) + throttle 30s.
- [ ] Tests en `tests/accounts/` y `tests/cli/` pasan.

## 18. Out of scope

- Ciclo del monitor (e03s02).
- Backfill (e04).

## 19. Risks (detailed)

- **Motor simulado en check**: T20.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/accounts/ tests/cli/test_accounts_cli.py` pasa.
- Tasks `status: passing` en `e03s01-tasks.yaml`.

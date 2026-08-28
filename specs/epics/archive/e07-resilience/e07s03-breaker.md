# Story e07s03 — Circuit breaker por cuenta (auth → paused + needs_review)

**type:** feat
**risk:** P0
**context:** domain
**BCPs:** 6
**status:** planned

## 1. Business narrative

Circuit breaker **por cuenta**: 5 fallos de **auth** consecutivos → `paused + needs_review` (§4.4). Los fallos transitorios NO cuentan (T5/T52); red/disco tampoco (T45/T64). El contador vive en memoria del proceso (se resetea al reiniciar); las pausas persisten en DB. Emite `monitor.account_paused` (F-08).

## 2. Actors

- **Motor de descarga** — reporta fallos al breaker.
- **Daemon / CLI** — ejecutan la ruta de descarga (el breaker vive en el proceso).
- **Usuario** — ve la cuenta paused + needs_review.

## 3. Problem statement

Sin breaker, una cuenta con auth rota reintenta infinitamente. Con breaker mal clasificado, cuentas sanas se pausan (T5: 403 sin auth = transitorio). El breaker debe contar SOLO fallos de auth reales (T52).

## 4. Requirements

#### ADDED: Circuit breaker por cuenta (§4.4)
**After:** `core/breaker.py` — `AccountBreaker` con contador en memoria por cuenta; 5 fallos de **auth** consecutivos → `paused + needs_review`; resetea el contador tras un éxito o un fallo no-auth.

#### ADDED: Solo fallos de auth cuentan (T5/T52)
**After:** Usa `classify_failure` (§4.3): auth markers (`requiring login`, `log into an account`, `log in for access`) → cuentan (T52); 403 sin auth → transitorio, NO cuenta (T5); red/disco → NO cuentan (T45/T64).

#### ADDED: Contador en memoria, pausa en DB (§4.4)
**After:** El contador del breaker vive en memoria del proceso (se resetea al reiniciar); las pausas persisten en DB (paused=True, needs_review=True).

#### ADDED: Evento monitor.account_paused (F-08)
**After:** El disparo del breaker emite `monitor.account_paused` (F-08).

## 5. Solution and main flow

1. `core/breaker.py`: AccountBreaker (contador en memoria, umbral 5).
2. Integración: `record_result(username, category)` — auth → +1; éxito → reset; transitorio/red/disco → no cuenta.
3. Pausa persistida (paused + needs_review) + evento.

## 6. Alternative flows / edge cases

- **Éxito tras fallos**: reset del contador.
- **Fallo transitorio**: no cuenta.
- **Reinicio**: contador reseteado (pausas persisten).

## 7. Assumptions

- `classify_failure` (e04s01) disponible.
- Modelo con paused/needs_review (e01s04).

## 8. Constraints

- Solo auth cuenta (T52); transitorio no (T5); red/disco no (T45/T64).
- Contador en memoria (§4.4).

## 9. Dependencies

- e04s01 (classify_failure), e01s04 (modelos), e03 (accounts).

## 10. Interfaces

- `core/breaker.py` → AccountBreaker.
- Consumido por motor (e04) y monitor (e03).

## 11. Test plan

- `tests/circuit/test_breaker.py`: 5 auth → paused+needs_review, transitorio no cuenta, reset en éxito, red/disco no cuenta, evento.

## 12. Data

- `monitored_accounts` (paused, needs_review).

## 13. Security considerations

- Sin secretos.

## 14. Performance

- Contador en memoria O(1).

## 15. Operational concerns

- Cuenta pausada + needs_review para revisión manual.

## 16. Risks

- **T5**: 403 sin auth no debe pausar (test).

## 17. Acceptance criteria

- [ ] 5 fallos auth consecutivos → paused + needs_review.
- [ ] Transitorios (T5/T52) NO cuentan; red/disco NO cuentan (T45/T64).
- [ ] Contador en memoria (reset en reinicio); pausas en DB.
- [ ] Evento monitor.account_paused (F-08).
- [ ] Tests en `tests/circuit/` pasan.

## 18. Out of scope

- Spool de notificaciones (ya en e06s02).
- Contención SQLite (e07s04).

## 19. Risks (detailed)

- **T5**: 403 sin auth transitorio.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/circuit/` pasa.
- Tasks `status: passing` en `e07s03-tasks.yaml`.

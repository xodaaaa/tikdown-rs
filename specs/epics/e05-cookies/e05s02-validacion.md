# Story e05s02 — Importación + validación triestado de cookies

**type:** feat
**risk:** P0
**context:** domain
**BCPs:** 9
**status:** planned

## 1. Business narrative

Importación de cookies (cifrado Fernet, best-effort T14/F-15) y validación en **tres estados** (valid/invalid/inconclusive, §7) con sonda configurable (T57/T74/R12). `get_working_cookie()` con revalidación activa y fallback (L-E3).

## 2. Actors

- **Usuario** — `cookies add/list/test/remove`.
- **Daemon** — validación periódica (COOKIE_VALIDATION_INTERVAL_SECONDS).
- **Motor** — get_working_cookie para descargas (e04).

## 3. Problem statement

Las cookies deben validarse sin invalidar recursos sanos: solo un fallo de auth confirmado → invalid; todo lo demás → inconclusive (sin tocar estados, F-16). La sonda debe ser robusta (T57/T74/R12).

## 4. Requirements

#### ADDED: Importación (F-15/T14/T73/T33)
**After:** `services/cookies.py`: `add(path, keep_source=False)` — cifra con Fernet, guarda en `encrypted_blob`; borrado fuente best-effort (T14, fallo no rompe éxito); `--keep-source` conserva (F-15); informa destino real. Tempfile Netscape con header (T73). Clamp de expiraciones absurdas a año 2100 (T33).

#### ADDED: Validación triestado (§7/F-16)
**After:** `validate_cookie()` → `valid`/`invalid`/`inconclusive`. Solo auth confirmado → invalid; extractor/red/timeout → inconclusive (NO toca validation_state ni last_validated_at, F-16). `no entries` → inconclusive.

#### ADDED: Sonda robusta (T57/T74/L-E4/R12)
**After:** Sonda itera `PROBE_MAX_ENTRIES=5` buscando formatos de vídeo (T74/L-E4); `COOKIE_VALIDATION_URL` acepta lista separada por comas con fallback en orden (R12); sonda rota (todas fallan) → ciclo inconclusive global sin tocar estados + `cookie.validation_probe_failed` (T57).

#### ADDED: get_working_cookie (L-E3/§7/T32)
**After:** Cookies `valid` ordenadas por `last_validated_at` desc; revalida activamente si el chequeo es antiguo; fallback si la primera falla; **solo rechaza ante `invalid`** (L-E3: inconclusive conserva con log). Sesiones cortas (T32: leer blob → cerrar sesión → validar → reabrir).

## 5. Solution and main flow

1. `services/cookies.py`: add/list/test/remove + get_working_cookie.
2. `services/cookie_validation.py` (o en cookies): sonda (T57/T74/R12).
3. CLI `cookies add/list/test/remove`.

## 6. Alternative flows / edge cases

- **Sonda rota**: inconclusive global (T57).
- **Primera entrada slideshow**: itera 5 (T74/L-E4).
- **Cookie inconclusive**: se conserva (L-E3).
- **Expiración corrupta**: clamp 2100 (T33).

## 7. Assumptions

- crypto/cookie_parser (e05s01), modelos (e01s04).

## 8. Constraints

- Triestado §7; F-16 (inconclusive no toca estados).
- Sonda itera 5 (T74); lista con fallback (R12).
- Sesiones cortas (T32).

## 9. Dependencies

- e05s01 (crypto, parser), e01s04 (modelos), e04 (motor).

## 10. Interfaces

- `services/cookies.py` → add/list/test/remove/get_working_cookie.
- `cli/cookies.py` → comandos.
- Consumido por motor (e04), daemon (validación periódica).

## 11. Test plan

- `tests/cookies/test_cookies.py`: import (F-15/T14), triestado (F-16), clamp (T33), L-E3.
- `tests/cookies/test_validation.py`: sonda T57/T74/R12, T32.

## 12. Data

- `cookies` (encrypted_blob, validation_state, last_validated_at).

## 13. Security considerations

- Cifrado en reposo; sonda rota no invalida (T57).

## 14. Performance

- Sesiones cortas (T32) evitan contención.

## 15. Operational concerns

- `cookie.validation_probe_failed` sugiere revisar COOKIE_VALIDATION_URL.

## 16. Risks

- **Invalidar cookie sana**: L-E3/F-16 (inconclusive conserva).

## 17. Acceptance criteria

- [ ] Import con F-15/T14; clamp T33.
- [ ] Triestado §7; F-16 (inconclusive no toca estados).
- [ ] Sonda itera 5 (T74/L-E4); lista fallback (R12); rota → inconclusive global (T57).
- [ ] get_working_cookie solo rechaza invalid (L-E3); revalida si antiguo; fallback.
- [ ] Sesiones cortas (T32).
- [ ] Tests en `tests/cookies/` pasan.

## 18. Out of scope

- Notificaciones Telegram (e06).
- Rotación en el motor (ya en e04).

## 19. Risks (detailed)

- **T57**: sonda rota nunca invalida cookies.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/cookies/` pasa.
- Tasks `status: passing` en `e05s02-tasks.yaml`.

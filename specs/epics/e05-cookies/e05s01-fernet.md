# Story e05s01 — Fernet en reposo (cifrado/descifrado de cookies)

**type:** feat
**risk:** P0
**context:** domain
**BCPs:** 5
**status:** planned

## 1. Business narrative

Las cookies de TikTok se guardan **cifradas en reposo** (Fernet) en `cookies.encrypted_blob` (LargeBinary, nunca Text — §2). `core/crypto.py` (e02s02) ya gestiona la clave (T7/T67/L-E2); esta story añade el **cifrado/descifrado** y el **parser de cookies** (T73: Netscape header).

## 2. Actors

- **Importador** — cifra cookies al importarlas.
- **Motor** — descifra para usar (e04).
- **Validación** — descifra para sondear (e05s02).

## 3. Problem statement

Sin cifrado, las cookies quedarían en claro en la DB. El cifrado debe: usar la clave 0600/O_EXCL (T7/T67), tolerar archivo vacío (L-E2), y el parser debe ser compatible con el `YoutubeDLCookieJar` real (T73).

## 4. Requirements

#### ADDED: Cifrado/descifrado Fernet de cookies
**After:** `core/crypto.py` añade `encrypt_cookie(blob, key) -> bytes` y `decrypt_cookie(ciphertext, key) -> bytes`. Ciphertext en `encrypted_blob` (LargeBinary).

#### ADDED: cookie_parser.py (T73)
**After:** `core/cookie_parser.py` con `NETSCAPE_HEADER` constante (`# Netscape HTTP Cookie File`), `write_netscape_file()` (con header + `newline="\n"`), parse de Netscape/JSON/cookie-string. El tempfile reconstruido debe cargarse con el `YoutubeDLCookieJar` REAL.

#### ADDED: Tempfile seguro (T31/L-H5)
**After:** `mkstemp` con ruta asignada justo tras creación + `os.close(fd)` inmediato + limpieza garantizada en `finally`.

## 5. Solution and main flow

1. `core/crypto.py`: encrypt_cookie/decrypt_cookie.
2. `core/cookie_parser.py`: NETSCAPE_HEADER, write_netscape_file, parse.
3. Tests con FERNET_KEY generada al vuelo (F-12).

## 6. Alternative flows / edge cases

- **Clave vacía en ventana de creación**: releer (L-E2, ya en e02s02).
- **Blob sin header**: write_netscape_file añade el header (T73).

## 7. Assumptions

- crypto.py (e02s02) con T7/T67/L-E2 ya implementado.

## 8. Constraints

- encrypted_blob = LargeBinary (nunca Text).
- Parser compatible con YoutubeDLCookieJar real (T73).
- Tempfile: mkstemp + os.close + finally (T31/L-H5).

## 9. Dependencies

- e02s02 (crypto.py clave), e01s04 (modelos).

## 10. Interfaces

- `core/crypto.py` → encrypt/decrypt_cookie.
- `core/cookie_parser.py` → NETSCAPE_HEADER, write_netscape_file, parse.
- Consumido por import (e05s02) y motor (e04).

## 11. Test plan

- `tests/crypto/test_fernet.py`: cifrado/descifrado roundtrip, LargeBinary, FERNET_KEY al vuelo.
- `tests/cookies/test_cookie_parser.py`: T73 (header, YoutubeDLCookieJar real), T31.

## 12. Data

- `cookies.encrypted_blob` (LargeBinary).

## 13. Security considerations

- Cifrado en reposo; clave 0600 (T7); backup fuera del repo (§0.1).

## 14. Performance

- N/A.

## 15. Operational concerns

- Selfcheck descifra una cookie real (T16, e02s02).

## 16. Risks

- **Parser incompatible con YoutubeDLCookieJar**: T73 (test con el jar real).

## 17. Acceptance criteria

- [ ] `encrypt_cookie`/`decrypt_cookie` roundtrip correcto.
- [ ] `NETSCAPE_HEADER` en cookie_parser (T73).
- [ ] `write_netscape_file` con header + newline="\n".
- [ ] Tempfile con mkstemp + os.close + finally (T31/L-H5).
- [ ] Tests pasan (clave generada al vuelo, F-12).

## 18. Out of scope

- Importación/validación (e05s02).
- Rotación de cookies (e05s02).

## 19. Risks (detailed)

- **Parser real**: test con YoutubeDLCookieJar.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/crypto/ tests/cookies/` pasa.
- Tasks `status: passing` en `e05s01-tasks.yaml`.

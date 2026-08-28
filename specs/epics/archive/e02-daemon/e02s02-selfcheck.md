# Story e02s02 — Selfcheck (impersonación TLS + ffmpeg/ffprobe + crypto)

**type:** feat
**risk:** P0
**context:** infra
**BCPs:** 5
**status:** planned

## 1. Business narrative

El daemon **no arranca** si la impersonación TLS no está operativa (§4.1). El selfcheck verifica: impersonación TLS (curl-cffi), ffmpeg/ffprobe (T46), y crypto Fernet (clave + descifrar cookie, T16). También introduce `core/crypto.py` (carga/generación de `fernet.key` con permisos 0600 y generación atómica O_EXCL).

## 2. Actors

- **Daemon runner** — ejecuta selfcheck en el arranque (fail-fast).
- **CLI `daemon selfcheck`** — selfcheck bajo demanda.
- **Operador** — lee el resultado (éxito / 3 causas de fallo).

## 3. Problem statement

Sin selfcheck, la impersonación rota produce 403 silenciosos en cada descarga (T6), ffmpeg ausente rompe el primer merge (T46), y una clave Fernet rotada aparece como fallos de auth aleatorios (T16). El selfcheck distingue las causas para un diagnóstico accionable.

## 4. Requirements

#### ADDED: selfcheck_impersonation() (§4.1)
**After:** `core/verify.py` define `selfcheck_impersonation()` — usa `_get_available_impersonate_targets` con try/except amplio (API interna no estable). Distingue **3 causas** (T6): (1) curl-cffi ausente → instalar `yt-dlp[default,curl-cffi]`; (2) versión no soportada por la nightly → pinear compatible; (3) targets vacíos pese a librería correcta → limitación de plataforma/API. Los targets son **objetos** ImpersonateTarget (L-D1). Sin targets → `SystemExit(1)`.

#### ADDED: sonda ffmpeg/ffprobe (T46)
**After:** El selfcheck verifica que `ffmpeg` y `ffprobe` son ejecutables (dependencia dura) y reporta versión.

#### ADDED: crypto Fernet con permisos 0600 + generación atómica (T7/T67)
**After:** `core/crypto.py`: carga/genera `fernet.key` con permisos **0600 también sobre clave existente** (T7, corregir con warning); generación **atómica con O_EXCL** (`open('xb')`, T67) — el perdedor relee la existente; tolera archivo vacío en la ventana de creación (releer, L-E2). Jerarquía: `FERNET_KEY` env → `DATA_DIR/fernet.key` → generar.

#### ADDED: selfcheck crypto (T16)
**After:** El selfcheck intenta **descifrar una cookie almacenada** con la clave activa — distingue "tabla ausente" (esquema sin migrar → informativo, no fallo) de error real de permisos/corrupción/bloqueo (→ FAIL). Compara versión yt-dlp con `yt_dlp.version.__version__` (T4).

## 5. Solution and main flow

1. `core/crypto.py`: cargar/generar fernet.key (0600, O_EXCL, vacío tolerado).
2. `core/verify.py`: selfcheck_impersonation (3 causas T6), sonda ffmpeg/ffprobe (T46), selfcheck crypto (T16, T4).

## 6. Alternative flows / edge cases

- **curl-cffi ausente / no soportado / targets vacíos**: 3 causas distintas (T6).
- **Tabla cookies ausente**: informativo, no fallo (T16).
- **Clave con 0644**: corregir a 0600 con warning (T7).
- **Archivo vacío en creación**: releer (L-E2).

## 7. Assumptions

- yt-dlp nightly instalado con curl-cffi (verificado e01s01).
- cryptography instalado.

## 8. Constraints

- Sin targets de impersonación → fail-fast (SystemExit).
- Comparar versión con `yt_dlp.version.__version__` (T4).
- Permisos 0600 siempre.

## 9. Dependencies

- e01s02 (Settings data_dir), e01s03 (logging), e01s04 (models).

## 10. Interfaces

- `core/verify.py` → selfcheck completo.
- `core/crypto.py` → load/generate fernet key.
- Consumido por daemon runner (e02s03).

## 11. Test plan

- `tests/daemon/test_selfcheck.py`: 3 causas de fallo (mock yt-dlp), éxito, ffmpeg/ffprobe, 0600, tabla ausente vs corrupción.
- `tests/crypto/test_crypto.py` (si aplica): carga/generación, O_EXCL, vacío.

## 12. Data

- `fernet.key` en DATA_DIR (nunca commitear, §0.1).

## 13. Security considerations

- Clave 0600 (T7); generación atómica (T67); selfcheck detecta rotación (T16).

## 14. Performance

- Selfcheck solo en arranque/bajo demanda (no en healthcheck, R10).

## 15. Operational concerns

- Fail-fast: daemon no arranca sin impersonación.

## 16. Risks

- **API interna de yt-dlp cambia**: try/except amplio (documentado en spec).

## 17. Acceptance criteria

- [ ] `selfcheck_impersonation()` distingue 3 causas (T6).
- [ ] Sonda ffmpeg/ffprobe (T46).
- [ ] `core/crypto.py` con 0600 (T7), O_EXCL (T67), vacío tolerado (L-E2).
- [ ] Selfcheck crypto distingue tabla ausente de error (T16).
- [ ] Versión yt-dlp vs `yt_dlp.version.__version__` (T4).
- [ ] Tests en `tests/daemon/test_selfcheck.py` pasan.

## 18. Out of scope

- Uso del selfcheck en el arranque del daemon (e02s03).
- Cifrado de cookies real (e05).

## 19. Risks (detailed)

- **Fragilidad de API interna**: try/except amplio es la única defensa (§4.1).

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/daemon/test_selfcheck.py` pasa.
- Tasks `status: passing` en `e02s02-tasks.yaml`.

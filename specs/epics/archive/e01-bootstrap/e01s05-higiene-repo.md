# Story e01s05 — Higiene de repo (README, LICENSE, .gitignore, .dockerignore, .env.example)

**type:** chore
**risk:** P1
**context:** infra
**BCPs:** 2
**status:** planned

## 1. Business narrative

El repositorio se publica en GitHub como open-source. La higiene de secretos se diseña **desde el primer commit** (§0.1): `.gitignore`, `.dockerignore` (T15), `.env.example` con solo valores de ejemplo, `LICENSE` (MIT), y un `README.md` con disclaimer legal, qué NO commitear, backup de `fernet.key` y nota de naming `-rs`.

## 2. Actors

- **Usuario / mantenedor** — publica el repo, despliega en Docker.
- **Docker build** — consume `.dockerignore` y `README.md` (F-04).
- **Nuevos contribuidores** — leen el README.

## 3. Problem statement

Sin `.dockerignore`, `COPY . .` embebe secretos (`.env`, `fernet.key`, `*.db`, cookies, `.git`) en capas Docker recuperables (T15). Sin `.env.example` con solo valores de ejemplo, se filtran secretos reales. Sin README con disclaimer, el proyecto queda expuesto a reclamaciones DMCA y a usuarios que commitean el volumen de datos.

## 4. Requirements

#### ADDED: .dockerignore completo (T15) re-incluyendo README.md (F-04)
**After:** `.dockerignore` cubre: `.env*`, `data/`, `videos/`, `*.db*` (incl. `*.db-journal`, `*.sqlite*`, `*.sqlite-wal`/`-shm`), `*.session*`, `fernet.key`, `cookies*.txt|json`, `.git/`, `.venv/`, `__pycache__/`. **README.md queda INCLUIDO** (no excluido — F-04: hatchling lo exige para el wheel).

#### ADDED: .gitignore completo (§0.1)
**After:** `.gitignore` cubre: `.env`, `*.db`, `*.db-wal`, `*.db-shm`, `data/`, `videos/`, `fernet.key`, `*.session`, `cookies*.txt|json`, `.venv/`, `__pycache__/`, `.migrate.lock`.

#### ADDED: .env.example con solo valores de ejemplo (§0.1)
**After:** `.env.example` documenta todas las variables de §12 con **solo valores de ejemplo o vacíos** — nunca tokens/chat IDs/claves reales. `WEBDAV_*` documentado como variables del sidecar (F-17).

#### ADDED: LICENSE MIT (§0.1)
**After:** `LICENSE` con MIT en la raíz (existe de seed; verificar contenido correcto).

#### ADDED: README.md completo (§0.1)
**After:** README incluye: qué es y cómo ejecutar (`uv run tikdown-rs daemon run`), **disclaimer legal estilo yt-dlp** (archivar contenido propio/permitido; responsabilidad del usuario), **qué NO commitear** (volumen de datos, cookies exportadas, `.env` real), **backup y recuperación de `fernet.key`** (respaldar fuera del volumen/repo; si se pierde, purgar `cookies` y reimportar), **nota de naming `-rs`** (histórico, proyecto es Python).

## 5. Solution and main flow

1. Crear `.dockerignore` (T15, incl. README F-04).
2. Revisar/completar `.gitignore`, `.env.example`, `LICENSE`, `README.md`.

## 6. Alternative flows / edge cases

- **Docker build exige README**: F-04 (no excluirlo del dockerignore).
- **Env vars sin efecto (T36)**: solo documentar las que Settings consume (o sidecar).

## 7. Assumptions

- Artefactos parciales existen de seed (gitignore/env.example/LICENSE/README); se completan.
- LICENSE MIT ya está; se verifica.

## 8. Constraints

- Nada de valores secretos reales en `.env.example`.
- `.dockerignore` no excluye README.md (F-04).
- Cobertura mínima de §0.1 en ambos ignore files.

## 9. Dependencies

- e01s01 (README mínimo), e01s02 (env vars), e01s04 (.migrate.lock).

## 10. Interfaces

- Docker build, git, nuevos contribuidores.

## 11. Test plan

- Verify de tarea: `test -f README.md && test -f LICENSE && test -f .dockerignore && grep -q "fernet.key" .gitignore && grep -q "cookies" .dockerignore`.

## 12. Data

Ninguno (solo archivos de higiene).

## 13. Security considerations

- Higiene de secretos desde el commit inicial (§0.1): la protección principal del repo público.

## 14. Performance

N/A.

## 15. Operational concerns

- `.env.example` como referencia; `.env` real nunca commiteado.

## 16. Risks

- **Secretos en historial git**: si algo se commiteó, reescribir con filter-repo antes del push público (§0.1).

## 17. Acceptance criteria

- [ ] `.dockerignore` existe, cubre T15 (env, db, cookies, fernet, .git, .venv) y **no excluye README.md** (F-04).
- [ ] `.gitignore` cubre §0.1 (`.env`, `*.db*`, `data/`, `videos/`, `fernet.key`, cookies, `.venv`, `.migrate.lock`).
- [ ] `.env.example` solo con valores de ejemplo/vacíos; `WEBDAV_*` como sidecar (F-17).
- [ ] `LICENSE` MIT presente.
- [ ] `README.md` con disclaimer legal, qué NO commitear, backup/recuperación de `fernet.key`, nota naming `-rs`.
- [ ] Verify de tarea pasa.

## 18. Out of scope

- Dockerfile real (e11/§11 — posterior).
- `.pre-commit-config.yaml` (F-22 — posterior).
- Woodpecker CI (F-22 — posterior).

## 19. Risks (detailed)

- **README excluido del dockerignore rompe el build**: F-04 (re-incluirlo).

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- Verify de tarea pasa.
- Tasks `status: passing` en `e01s05-tasks.yaml`.

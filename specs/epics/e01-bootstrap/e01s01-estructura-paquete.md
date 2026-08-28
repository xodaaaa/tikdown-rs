# Story e01s01 — Estructura del paquete y toolchain uv

**type:** feat
**risk:** P1
**context:** infra
**BCPs:** 3
**status:** planned

## 1. Business narrative

TikDown-rs necesita un esqueleto de proyecto Python 3.13 reproducible y publicable: layout del paquete, `pyproject.toml` con las dependencias del stack (pines exactos de §1.1), y toolchain `uv` que genere un `uv.lock` determinista. Sin esto, ninguna story posterior (config, modelos, daemon) tiene dónde vivir ni cómo ejecutarse.

## 2. Actors

- **Desarrollador** — ejecuta `uv run`, `uv sync`, `uv lock`, `uv run pytest`.
- **Herramientas CI/Docker** — consumen `pyproject.toml` y `uv.lock` para builds reproducibles.

## 3. Problem statement

No existe ningún artefacto de proyecto: ni `pyproject.toml`, ni `uv.lock`, ni paquete importable. El primer paso de cualquier feature es un esqueleto que fije Python 3.13 y las dependencias correctas, sin pines abiertos que rompan la reproducibilidad.

## 4. Requirements

#### ADDED: Layout del paquete plano (src layout NO aplica)
**After:** Estructura de §13: `core/`, `services/`, `cli/`, `daemon/`, `models/`, `tests/` en la raíz del paquete; `[project.scripts] tikdown-rs = "cli.main:run"` (lección L-A2). El plan §13 NO usa src layout.

#### ADDED: pyproject.toml con dependencias pineadas
**After:** `[project] name="tikdown-rs"`, `requires-python = ">=3.13,<3.14"`, dependencias de §1.1 (yt-dlp nightly con extra `pin-curl-cffi`, curl-cffi `==`, aiosqlite `>=0.22.1` excluyendo `0.22.0`, SQLAlchemy `>=2.0.51,<2.1`, APScheduler `>=3.11,<4`, PTB `[rate-limiter]>=22`, typer, rich, pydantic-settings, cryptography, httpx, alembic).

#### ADDED: [tool.uv] prerelease-package solo para yt-dlp
**After:** `[tool.uv] prerelease-package = { "yt-dlp" = "allow" }` — NUNCA `prerelease = "allow"` global (T2, riesgo de cadena de suministro).

#### ADDED: .python-version fija el minor
**After:** `.python-version` con `3.13` (mismo minor en dev y Docker).

#### ADDED: Toolchain uv funcional
**After:** `uv sync` instala, `uv lock` genera `uv.lock`, el paquete vacío es importable (`import tikdown_rs` no lanza).

## 5. Solution and main flow

1. Crear directorios del paquete (`core/`, `services/`, `cli/`, `daemon/`, `models/`, `tests/`) con `__init__.py`.
2. Escribir `pyproject.toml` (pines §1.1) + `.python-version`.
3. Ejecutar `uv lock` → genera `uv.lock` determinista.
4. Ejecutar `uv sync` → instala el venv.
5. Verificar importabilidad del paquete.

## 6. Alternative flows / edge cases

- **yt-dlp nightly no encontrado**: si el identificador exacto no existe en PyPI, resolver el nightly más reciente en formato `YYYY.MM.DD.HHMMSS` y fijarlo (procedimiento §1.2).
- **curl-cffi serie incompatible**: si el extra `pin-curl-cffi` no está disponible en la nightly elegida, usar pin manual exacto compatible (fallback del plan §1).
- **uv no presente**: instrucción de instalación en el README (fuera de esta story).

## 7. Assumptions

- `uv >= 0.12` disponible en el entorno de desarrollo (verificado: uv 0.12.7).
- El entorno de implementación tiene acceso a PyPI.

## 8. Constraints

- Python `>=3.13,<3.14` (fijado por `requires-python` y `.python-version`).
- Pines exactos para yt-dlp y curl-cffi; nunca rangos abiertos.
- `prerelease-package` solo para yt-dlp (T2).

## 9. Dependencies

- Ninguna story previa (es la primera de e01).

## 10. Interfaces

- `pyproject.toml` → consumido por uv, CI, Docker.
- `.python-version` → consumido por uv.

## 11. Test plan

- `uv lock` termina con éxito y genera `uv.lock` (reproducibilidad).
- `uv sync` + `import tikdown_rs` no lanza (importabilidad).

## 12. Data

Ninguno (no toca base de datos ni modelo de datos).

## 13. Security considerations

- Sin pines globales abiertos (T2) — riesgo de cadena de suministro.
- Sin secretos en el esqueleto.

## 14. Performance

N/A (esqueleto).

## 15. Operational concerns

- El lock debe regenerarse ante cualquier cambio de pin (procedimiento §1.2).

## 16. Risks

- **Pin de nightly no reproducible**: mitigación con pin exacto de fecha + `uv lock` (R1 en §Risks de tasks).

## 17. Acceptance criteria

- [ ] `pyproject.toml` existe con `name="tikdown-rs"`, `[project.scripts]` y dependencias de §1.1.
- [ ] `[tool.uv] prerelease-package = { "yt-dlp" = "allow" }` presente; sin `prerelease = "allow"` global.
- [ ] `.python-version` = `3.13`.
- [ ] Layout del paquete plano (§13) existe con `__init__.py`.
- [ ] `uv lock` genera `uv.lock`.
- [ ] `uv sync` + `import tikdown_rs` no lanza.

## 18. Out of scope

- Contenido real de `core/config.py`, `models/`, etc. (stories e01s02+).
- `alembic.ini`, Dockerfile, pre-commit, woodpecker (stories posteriores de e01).

## 19. Risks (detailed)

- **Pin de nightly no reproducible**: mitigado por pin exacto de fecha + `uv lock` (procedimiento §1.2).

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv lock` + `uv sync` + `import tikdown_rs` pasan.
- Tasks `status: passing` en `e01s01-tasks.yaml`.

# Story e10s01: Pipeline Woodpecker (.woodpecker.yml): ruff, pytest+cobertura, build Docker+smoke

## 1. Metadata

| Campo | Valor |
|-------|-------|
| story_id | e10s01 |
| epic | e10-ci |
| type | feat |
| risk | P1 |
| context | infra |
| bcps | 3 |
| delta | ADDED |

## 2. Título

CI Woodpecker: pipeline con ruff, pytest con cobertura y build Docker + smoke.

## 3. Problema

El repo está publicado (github.com/xodaaaa/tikdown-rs, privado) sin CI activo. Los gates de calidad
(pytest, ruff, build Docker) se ejecutan solo en local; no hay verificación automática en push/PR.

## 4. Contexto

`state.yaml` → `active_flow: null`, `handoff.next_skill: survey-context`. El plan maestro §1.3
documenta el CI deseado: ruff, pytest, cobertura y build Docker multi-arquitectura + smoke
(F-22, L-K4). El proyecto usa uv + pyproject.toml ya configurado (pytest, coverage, ruff).

## 5. Alcance

- Añadir `.woodpecker.yml` en la raíz del repositorio.
- Pipeline con 3 pasos: lint (ruff), test (pytest + cobertura), docker (build + smoke).
- Documentar L-K4 (fallo en 0s = runner/billing, no código) dentro del archivo.

## 6. Fuera de alcance

- Activación real del repo en un runner Woodpecker (manual en la UI, no automatizable aquí).
- Multi-arquitectura buildx (amd64+arm64) — requiere runner con buildx; se deja documentado.
- Jobs programados pip-audit/trivy (T76) — backlog, no en este epic.
- Migración a otro CI (GitHub Actions, etc.).

## 7. Stack y dependencias

- Woodpecker CI: `.woodpecker.yml` (sin dependencias nuevas; YAML parseado por el runner).
- `uv` como gestor; `ruff`, `pytest`, `coverage` ya en `[dependency-groups] dev` — **sin nuevas dependencias**.
- Docker para el build de imagen + smoke.

## 8. Diseño

`.woodpecker.yml` con un pipeline por defecto y 3 steps:
- **lint**: `uv run ruff check .` + `uv run ruff format --check .`
- **test**: `uv run pytest --cov=tikdown_rs --cov-report=term --cov-report=xml:coverage.xml`
- **docker**: `docker build -t tikdown-rs:ci .` + `docker run --rm tikdown-rs:ci tikdown-rs --version`

Cada step con `when: event: [push, pull_request]`. Imagen base del runner: `python:3.13` +
docker (socket montado) para el step docker.

## 9. Requisitos

### ADDED: Pipeline CI Woodpecker
**After:** `.woodpecker.yml` en la raíz define 3 pasos (lint, test, docker) ejecutados en push y
pull_request, con cobertura XML generada y smoke de la imagen Docker.

### ADDED: Lección L-K4 documentada
**After:** El archivo incluye un comentario: un fallo en 0s de todos los pasos suele indicar
billing/configuración del runner, no un fallo del código.

## 10. Comportamiento

El pipeline:
1. En **push/PR**: ejecuta ruff (check + format check).
2. Ejecuta pytest con cobertura (term + XML) — falla si hay tests rojos o cobertura por debajo
   del umbral (si se configura `--cov-fail-under`; por defecto sin umbral para no romper el MVP).
3. Construye la imagen Docker y ejecuta `tikdown-rs --version` dentro de ella (F-22).

## 11. Pasos de implementación

1. Añadir `.woodpecker.yml` (lint + test + docker + smoke, when: push/PR, comentario L-K4) → verify: `test -f .woodpecker.yml && grep -q 'ruff check' .woodpecker.yml && grep -q '--cov' .woodpecker.yml && grep -q 'docker build' .woodpecker.yml && grep -q 'docker run --rm' .woodpecker.yml`
2. Validar YAML parseable → verify: `uv run python -c "import yaml; yaml.safe_load(open('.woodpecker.yml')); print('yaml OK')"`
3. Verificación local equivalente → verify: `uv run ruff check . && uv run ruff format --check . && uv run pytest --cov=tikdown_rs --cov-report=term -q && docker build -q -t tikdown-rs:ci . && docker run --rm tikdown-rs:ci tikdown-rs --version`

## 12. Script de verificación (step-by-step)

1. `git status` → working tree limpio antes de empezar.
2. `cat .woodpecker.yml` → revisar que los 3 pasos y el comentario L-K4 están presentes.
3. `uv run ruff check . && uv run ruff format --check .` → todos los checks pasan.
4. `uv run pytest --cov=tikdown_rs --cov-report=term -q` → 184 tests pasan con cobertura.
5. `docker build -q -t tikdown-rs:ci . && docker run --rm tikdown-rs:ci tikdown-rs --version` → imagen construida y versión impresa (smoke F-22).
6. `uv run python -c "import yaml; yaml.safe_load(open('.woodpecker.yml'))"` → YAML válido.

## 13. Criterios de aceptación

- [ ] `.woodpecker.yml` existe en la raíz con 3 pasos (lint, test, docker).
- [ ] El step lint ejecuta `ruff check` y `ruff format --check`.
- [ ] El step test ejecuta pytest con `--cov` y genera `coverage.xml`.
- [ ] El step docker ejecuta `docker build` y `docker run --rm ... tikdown-rs --version` (smoke F-22).
- [ ] El comentario L-K4 está presente en el archivo.
- [ ] Todos los comandos del pipeline pasan en local.

## 14. Definición de éxito

`git status` limpio, `.woodpecker.yml` presente con los 3 pasos, y la verificación local
equivalente (ruff + pytest + docker smoke) exitosa.

## 15. Saliendo

- git branch `feat/e10-ci-woodpecker` creado vía kickoff-branch.
- Commit único con mensaje Conventional Commits (`feat(ci): ...`).

## 16. Riesgos

| Riesgo | Detección |
|--------|-----------|
| L-K4: fallo en 0s en Woodpecker = billing/configuración del runner | Verificación local equivalente pasa; el fallo remoto es del runner, no del código |
| El smoke Docker no detecta problemas de DB (F-22) | El smoke es condición necesaria no suficiente; cubierto en backlog |
| YAML inválido en el runner | Validación local con `yaml.safe_load` |

## 17. Criterios de aceptación (checklist)

- [ ] Story spec escrita en `specs/epics/e10-ci/e10s01-ci-woodpecker.md`
- [ ] Tasks en `e10s01-tasks.yaml` con `status: failing` (no pre-marcados)
- [ ] `.woodpecker.yml` creado y validado

## 18. Seguimiento

- Estado: `failing` (tasks) → `passing` tras verify-work.
- `state.yaml` `active_flow` → `build_epic` durante ejecución.

## 19. Notas

- L-K4 documentado: fallo en 0s de todos los workflows = billing/configuración de runner.
- F-22 documentado: `docker run --rm ... tikdown-rs --version` detecta problemas de imagen que el build solo no ve.
- Activación real del repo en Woodpecker es manual (UI del runner).

## 20. Riesgo (técnico)

P1 — infraestructura; sin datos de usuario, sin lógica de negocio. El fallo de CI no bloquea el
código, solo la visibilidad.

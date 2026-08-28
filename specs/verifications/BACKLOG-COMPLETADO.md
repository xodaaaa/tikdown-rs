# Backlog Completado — TikDown-rs v0.2.0

## Fecha de finalización

2026-08-28 (sesión de backlog cerrada)

## Resumen de los 3 items

| # | Item | Epic/Story | Bump | Estado |
|---|------|-----------|------|--------|
| 1 | **CI Woodpecker** — pipeline ruff, pytest con cobertura, build Docker + smoke | e10s01 | minor | ✅ Liberado |
| 2 | **Paginación en /list del bot** — botones inline ◀️/▶️ | e11s01 | minor | ✅ Liberado |
| 3 | **Supervisión del polling de Telegram** — healthcheck getMe + reconexión | e12s01 | minor | ✅ Liberado |

## Métricas (verificadas en vivo)

| Check | Resultado |
|-------|-----------|
| Tests | **219 passed** (184 MVP → 219, +35 nuevos) |
| Lint | **All checks passed** (ruff check + format) |
| Cobertura | 63% total (puntos calientes > 82%) |
| Commits | 12 commits convencionales (3 test + 3 feat + 3 chore + 3 docs) |

## Detalle por item

### 1. CI Woodpecker (e10s01) — `feat(ci)`
- `.woodpecker.yml` en la raíz: 3 steps (lint ruff, test pytest+cov, docker build+smoke)
- `when: [push, pull_request]`; L-K4 documentado (fallo en 0s = runner/billing)
- F-22: smoke `docker run --rm ... tikdown-rs --version` (verificado: `tikdown-rs 0.1.0`)
- Deps dev añadidas: `pytest-cov`, `pyyaml`

### 2. Paginación /list (e11s01) — `feat(telegram)`
- `render_list_page` + `build_list_keyboard` (botones ◀️ Anterior / Siguiente ▶️)
- T38: callback_data `listp:{ts}:{page}` ≤ 64 bytes (test)
- §6.3: expiración real 60s (timestamp validado, `now` inyectable)
- F-18: throttle 2s por chat en callbacks; authz doble; tolera updates sin chat

### 3. Supervisión polling (e12s01) — `feat(telegram)`
- T71: healthcheck `getMe` periódico (30s), nunca `getUpdates`
- Tras 3 fallos → reinicio del bot (stop + start) sin reiniciar el daemon
- PTB #3430: detección vía getMe (empírica), no `add_error_handler`
- Tarea supervisada (T27), cancelada en stop (T28); flag anti-reinicio-concurrente
- `run.py` ahora usa `TelegramBot` (antes Application propia sin supervisión)

## Publicación

- Repositorio: **privado** — https://github.com/xodaaaa/tikdown-rs
- Tag: **v0.2.0** en el remoto (commit `a87544e`, cierre del backlog)
- Rama `master` sincronizada, working tree limpio

## Estado final

- `specs/state.yaml`: `release.last_tag: v0.2.0`, `active_flow: null`, `next_skill: survey-context`
- Epics e10/e11/e12 archivados en `specs/epics/archive/`
- Siguiente paso: `survey-context` para el próximo ciclo de desarrollo

# TikDown-rs — AI Agents

Read CONVENTIONS.md before any GitHub or git operation.

<!-- BEGIN bigpowers:project -->
## Project
CLI + Daemon + Telegram para archivar vídeos de TikTok de forma automatizada y resistente a bloqueos.
Stack: Python 3.13 / uv / SQLAlchemy async + aiosqlite / yt-dlp nightly + curl-cffi / python-telegram-bot / typer / rich / cryptography / APScheduler

## Commands
| Action | Command |
|--------|---------|
| Run    | `uv run tikdown-rs daemon run` |
| Test   | `uv run pytest` |
| Build  | `uv build` |
| Lint   | `ruff check .` |
| Preflight | `uv run pytest && ruff check . && uv build` |
| CI     | `gh pr checks` (when a PR is open) |

## Architecture
CLI + Daemon + Bot de Telegram sobre el mismo código base, coordinados vía SQLite (WAL). Lógica de negocio en `services/`, modelos en `models/`, comandos en `cli/`, sin servidor HTTP.

## Conventions
- Async-first: toda llamada bloqueante (yt-dlp, SHA-256, ffprobe) va a `asyncio.to_thread`.
- `services/*` nunca importa `yt_dlp`, `typer` ni el SDK del bot.
- Lógica real en `services/*`; `cli/` y el bot solo orquestan.
- Logging stdlib con formatter JSON; nunca structlog.
- Toda tarea de fondo pasa por `create_supervised_task()`; nunca `asyncio.create_task` directo.

## Never
- Never dismiss reproducible gate failures as pre-existing or out of scope
- Never proceed on red Preflight or red CI — invoke quick-fix or fix-bug first
- NEVER use httpx/requests contra dominios de TikTok — solo yt-dlp + curl-cffi
- NEVER usar structlog — logging stdlib
- NEVER exponer un servidor HTTP ni frontend
- NEVER commitear secretos, `.env`, `*.db`, cookies, `fernet.key` ni el volumen de datos
<!-- END bigpowers:project -->

## Agent Rules
- **Workflow Mandate:** You MUST use the bigpowers skills (e.g. `plan-work`, `develop-tdd`, `orchestrate-project`) to perform tasks. DO NOT write code directly in response to a user prompt like "build this feature".
- **Always Green:** Preflight and CI must be green before forward work. Reproducible gate failures require **fix-or-log** (quick-fix → fix-bug) per CONVENTIONS § Discovered Defects.
- Read specs/ before writing code.
- All planning and specifications MUST be written to `specs/` (`product/SCOPE_LATEST.yaml`, `release-plan.yaml`, `epics/`) before any code is generated.
- Write the minimum code that solves the stated problem. Nothing extra.
- Run tests after every change. Show evidence before declaring done.
- One clarifying question beats a wrong assumption baked into 200 lines.

<!-- BEGIN bigpowers:context-routing -->
## Context Routing
| Glob | Instructions file |
|------|-------------------|
| `services/**`, `cli/**`, `models/**`, `daemon/**` | This file |
<!-- END bigpowers:context-routing -->

<!-- BEGIN bigpowers:learned-preferences -->
## Learned User Preferences
<!-- Agent: append preferences here as you learn them. -->

## Workspace Facts
<!-- Agent: append workspace facts here as you discover them. -->
<!-- END bigpowers:learned-preferences -->

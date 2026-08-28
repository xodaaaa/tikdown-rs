# Tech Stack — TikDown-rs

> Documento de arranque (seed). Se completa con map-codebase / deepen-architecture a medida que el código exista.

## Stack

| Categoría | Elección |
|-----------|----------|
| Lenguaje | Python 3.13 |
| Gestor | uv |
| ORM/DB | SQLAlchemy 2.0.x async + aiosqlite + Alembic (WAL) |
| Descarga | yt-dlp (nightly, pin exacto) + curl-cffi (pin exacto, extra pin-curl-cffi) |
| Bot | python-telegram-bot[rate-limiter] ≥22 |
| CLI | typer ≥0.27 + rich ≥15 |
| Cifrado | cryptography (Fernet) |
| Scheduler | APScheduler 3.x AsyncIOScheduler |
| HTTP | httpx (solo Bot API y probe de red — NUNCA TikTok) |
| Logging | logging stdlib + formatter JSON ad-hoc |
| Lint/Test | ruff / pytest + pytest-asyncio + coverage |

## Arquitectura

CLI + Daemon + Bot de Telegram sobre el mismo código base. Coordinación vía SQLite (WAL): `daemon_state`, `download_pacing_state`. Sin servidor HTTP.

- `services/` — lógica de negocio reutilizable (nunca importa yt_dlp/typer/SDK bot)
- `models/` — modelos SQLAlchemy
- `cli/` — comandos typer, wrappers `asyncio.run()` en `cli/common.py`
- `daemon/` — proceso de larga duración (APScheduler + bot en el mismo event loop)
  - `daemon/telegram/` — bot de Telegram (dispatcher + handlers), alojado dentro del daemon
- `migrations/` — Alembic

## Gray areas (pendientes de resolver)

- Estructura exacta del paquete Python (src layout) — a decidir en plan-work
- Detalles de pin de yt-dlp nightly / curl-cffi — reverificar contra PyPI en plan-work (§1.2)

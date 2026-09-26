# tikdown-rs

Self-hosted TikTok archive daemon: monitor accounts, backfill videos, and expose status
through a CLI and a Telegram bot.

- Python 3.13, managed with [uv](https://docs.astral.sh/uv/)
- Storage: SQLite via SQLAlchemy + Alembic
- CLI: `uv run tikdown-rs --help`

Legal: this tool downloads publicly available content; respect creators' rights and the
platform's terms of service. Do not commit `.env`, `cookies*`, `*.db*`, or `videos/`.

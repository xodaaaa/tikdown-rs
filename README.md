# tikdown-rs

Self-hosted TikTok archive daemon: monitor accounts, backfill videos, and expose status
through a CLI and a Telegram bot.

- Python 3.13, managed with [uv](https://docs.astral.sh/uv/)
- Storage: SQLite via SQLAlchemy + Alembic
- CLI: `uv run tikdown-rs --help`

Legal: this tool downloads publicly available content; respect creators' rights and the
platform's terms of service. Do not commit `.env`, `cookies*`, `*.db*`, or `videos/`.

## Operation

- **Status & health**: `daemon status` (heartbeat, monitor, cookies by state, disk, recent
  errors), `daemon healthcheck` (Docker `HEALTHCHECK`: light, no network), `daemon selfcheck`
  (full check, compares yt-dlp against `last_known_good_ytdlp_version`).
- **Disk**: `system disk [--resume]` — free space, threshold, `downloads_paused`.
- **Backup**: `system backup` writes a `VACUUM INTO` snapshot to `<DATA_DIR>/backups/`
  (online, safe while the daemon runs; retention `SYSTEM_BACKUP_RETAIN_COUNT`, files `chmod 0600`).
  The DB contains cookies unencrypted — treat backups as secrets (§15.2).
- **Restore**: stop the daemon, copy the snapshot over `tikdown-rs.db`, delete `-wal`/`-shm`,
  start again, run `daemon selfcheck`, then `videos integrity` (§14.5).
- **Integrity**: `videos integrity [username]` — size + SHA-256 + ffprobe per archive file
  (read-only diagnostic; missing ffprobe downgrades to a warning).
- **Export**: `videos export [--format json|csv]` (CSV sanitized against formula injection).
- **Static dashboard**: `system site render` writes `index.html` + 3 JSONs to `STATIC_SITE_DIR`
  (default `<DATA_DIR>/public`). No server, no port: point nginx/Caddy/any static host at that
  directory. The daemon can regenerate it every `STATIC_SITE_INTERVAL_MINUTES` with
  `STATIC_SITE_ENABLED=true`. Never expose the SQLite/cookies volume together with it.

## Troubleshooting

Diagnose in this order (§14.6):

1. **A transient failure is not a defect** — extractor degradation, WAF challenges, 403/429 and
   timeouts classify as `transient`: they pause no accounts, invalidate no cookies, and consume no
   retries. Check the category in `daemon status` → `recent_errors` first.
2. **After a yt-dlp bump, the bump is the default suspect** — `daemon selfcheck` compares against
   `last_known_good_ytdlp_version`.
3. **Every fixed bug adds a row** to the plan's troubleshooting tables and, if it revealed an
   undocumented trap, to Appendix A (§16, rule 10).

Full symptom tables (first boot/deploy, TikTok interactions, cookies, Telegram): see
`specs/Plan de implementacion tikdown-rs.md` §14.6.

# M0 — Base

Source of truth: `specs/Plan de implementacion tikdown-rs.md` (§12.1 M0 row, §2.3, §5.1–§5.5, §10, §11, §13.2, §14.1, Apéndice A/B).

## Milestone acceptance (§12.1)

- `docker run --rm <img> tikdown-rs --version` OK
- `daemon run` starts, writes heartbeat, shuts down cleanly with `daemon stop`

## Tasks

- [x] T1 — ODD tracking created (this file + Engram mirror `odd/m0-base/tasks`)
- [x] T2 — §2.3 pin reverification (live: yt-dlp nightly, curl-cffi extra, wheels amd64/arm64; constraints kept: aiosqlite != 0.22.0, SQLAlchemy >=2.0.51,<2.1, APScheduler >=3.11,<4, prerelease scoped to yt-dlp). Results recorded in plan Apéndice B (reverificación 2026-09-26). SQLAlchemy <2.1 kept as explicit decision (2.1.1 now stable; relaxing deferred, user informed).
- [x] T3 — Initial commit on master (repo hygiene: .gitignore, specs/, odd/) + feature branch `feature/m0-base`. Commit `089b6ec`; git identity user.name `PansitoDeMichi`, noreply email, repo-local.
- [x] T4 — WU1: pyproject + uv lock + scaffold (README, LICENSE, .env.example, .dockerignore, .python-version) + architecture test (services/* imports). Commit `d1a6e28`.
- [x] T5 — WU2: core/config (Settings fail-fast §11.1, unknown-var warning) + core/logging (JSON §5.7) + tests red-first. Commit `fe378b4` (RED->GREEN observed: 14 new tests; DR-17 applied inline: ANTIBOT vars retired).
- [x] T6 — WU3: CLI skeleton 7 groups (§10.1, T-CLI-1..5) + cli/common (run_or_exit) + smoke tests. Commit `a7a3f94` (29 commands, RED->GREEN).
- [x] T7 — WU4: core/db (PRAGMAs §3.7, T-DB-5/9) + core/migrations idempotent (§5.4, T-DEPLOY-1..4, T-DB-10) + alembic env async (T-DEPLOY-2) + initial migration with marker table `daemon_state` + tests. Commit `8c65b70` (20 new tests RED->GREEN; production caller pending WU5 by design).
- [x] T8 — WU5: daemon/run minimal lifecycle (§5.1 order, §5.2, heartbeat job, stop watcher, T-CLI-6/7, T-ASYNC-3) + `daemon stop/status/healthcheck` + tests red-first. Commit `69aec68` (12 mandated cases incl. real subprocess stop test; T-DEPLOY-6 disabled-logger gap found by worker and fixed inline with regression test).
- [x] T9 — WU6: Dockerfile multi-stage uv pattern (§14.1, T-DEPLOY-5/11/12/13/14) + docker-compose + docker build smoke. Commit `75a788a` (includes live-smoke bug fix: WORKDIR/COPY ordering -> T-DEPLOY-23).
- [x] T10 — Gate final: ruff check / ruff format --check / pytest green / CLI smokes / docker smoke. All green: 107 passed.

## Evidence log

- T1: this file + Engram mirror created before any source write.
- T3: commit `089b6ec` (master); branch `feature/m0-base`; git identity PansitoDeMichi (noreply, repo-local).
- T4: commit `d1a6e28`; worker (gentle-ai-worker); gate green: pytest 2 passed, ruff clean, `uv run tikdown-rs --version` OK; lock: yt-dlp 2026.9.16.232951.dev0, sqlalchemy 2.0.54, alembic 1.20.0, aiosqlite 0.22.1, apscheduler 3.11.3.
- T5: commit `fe378b4`; worker (gentle-ai-worker); RED was ModuleNotFoundError on core.config; 16 passed after DR-17 inline fix; gate green.
- T6: commit `a7a3f94`; worker; 58 tests green; command tree 1:1 with §10.1 (29 incl. site render).
- T7: commit `8c65b70`; worker; suite 78 green; T-DB-10 two-revision regression deferred to M1 when a second migration exists (single-revision stamp path tested).
- T8: commit `69aec68`; worker; suite 93 green; subprocess stop smoke 2.2s; real subprocess T-CLI-6 case covered; T-DB-10 two-revision regression still deferred to M1.
- T9: commit `75a788a`; worker (Dockerfile/compose/12 static guards) + live build by parent; root-cause found live: relative COPY before WORKDIR -> files in `/` (rule 10 §16 applied: regression test + T-DEPLOY-23 + §14.6 row). Live acceptance: --version OK, heartbeat written, daemon stop -> Exited(0), alembic_version=0001_daemon_state in volume DB.
- T10: final gate 2026-09-26: ruff clean, format clean, pytest 107 passed, 11 CLI smokes OK, docker --version OK. M0 acceptance criteria met.
- Close: user delegated merge decision (2026-09-26). Merging feature/m0-base -> master with --no-ff; branch deleted after merge. Next milestone: M1 (Datos y cookies) — proposed, not started.

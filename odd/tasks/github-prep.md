# Feature: GitHub-prep — preparation for eventual public upload

Repo starts private; will become public at some point. Goal: sanitize the repo of
sensitive material (personal paths, secrets, local artifacts) and produce
project-facing guides (project history guide + detailed Docker deployment guide)
so the eventual public version is publish-ready.

Constraints decided with the user:
- Include: complete guide of how the project evolved (project-level
  inconveniences, NOT agent/tooling anecdotes), with full detail.
- Include: Docker implementation guide with full detail.
- Sanitation: treat every personal path, real credential, or local artifact as
  publishable surface. `odd/` and `specs/` stay for now (private phase); final
  public scope revisited at upload time.

## Tasks

- [x] T1 — Cleanup: remove `.tmp-docker-data;C` (empty local dir), add `/data/`
      to `.gitignore` (compose mounts `./data` on host; `/app/data/` only covers
      in-container). Commit: 90fd292
- [x] T2 — Sanitize personal path in `odd/tasks/m6-operacion.md`
      (`$HOME/...` → generic worktree path). Commit: 90fd292
- [x] T3 — Sensitivity audit: tracked tree + git history. Findings: no
      tokens/secrets; emails are GitHub noreply only. One polluted string (the Windows
      username in `odd/tasks/m6-operacion.md`) existed in history commits `b08c96a`
      and `90fd292^` — tracked tree is clean; history rewrite recorded as upload-time
      pending decision (see section below). Commit: 90fd292
- [x] T4 — Docker deployment guide `docs/deployment-docker.md` (es): build pattern,
      compose walkthrough (every hardening option explained), first boot, cookies,
      backfill, stop semantics, dashboard, updates/yt-dlp bump protocol, backup/restore,
      first-boot symptoms. Written from the real Dockerfile/docker-compose.yml. Commit: 6ca4658
- [x] T5 — Project guide `docs/project-guide.md` (es): evolution by milestone M0-M6,
      project-level inconveniences and decisions, live-round bugs, review results,
      final state, lessons. Derived from specs Apéndices A/C + git log + odd ledgers. Commit: 6ca4658
- [x] T6 — Final verification via `gentle-ai-verify`: pytest exit 0 (only the 2
      `live` markers skipped), `ruff check` + `ruff format --check` clean,
      `git status` shows exactly the two new docs, tracked tree greps clean of
      personal paths, both docs valid UTF-8. Commit: —

- [x] T7 — README restructured following Azure Samples / sinedied README patterns:
      centered header with badges + nav, GFM alerts (`[!CAUTION]`, `[!IMPORTANT]`, `[!TIP]`),
      features bullets, deploy quickstart verified against the real CLI, trimmed Operation
      section, docs table, image credit kept at bottom. CLI paths re-checked against
      `src/tikdown_rs/cli/` before publishing. Commit: 305bbe0

## Pendientes para el momento de publicar (decisión del usuario, no hago nada automático)

- Revisar alcance público de `odd/` y `specs/` (hoy quedan; pueden salir o
  depurarse). Si deciden salir del repo, la historia del path de Windows importa
  menos.
- La historia de git contiene el nombre de usuario de Windows en
  `odd/tasks/m6-operacion.md` (commits `b08c96a` en adelante, corregido en el
  árbol desde `90fd292`). Opciones antes de publicar: `git filter-repo` sobre ese
  archivo, o publish con otra historia (squash). No hay remote ni push todavía,
  así que no urge — es decisión del momento de publicar.
- Re-verificar `.env`/cookies/db fuera del árbol al crear el repo en GitHub
  (apuntando al repo remote correcto, no copiar el local).

Branch: `feat/github-prep`

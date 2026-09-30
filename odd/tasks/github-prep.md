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

- [ ] T1 — Cleanup: remove `.tmp-docker-data;C` (empty local dir), add `/data/`
      to `.gitignore` (compose mounts `./data` on host; `/app/data/` only covers
      in-container).
- [ ] T2 — Sanitize personal path in `odd/tasks/m6-operacion.md`.
- [ ] T3 — Sensitivity audit: tracked tree + git history (tokens, paths, emails).
- [ ] T4 — Docker deployment guide in `docs/deployment-docker.md`, written from
      the real `Dockerfile` and `docker-compose.yml`, not generic.
- [ ] T5 — Project guide in `docs/project-guide.md`: evolution of the project,
      project-level decisions and inconveniences, derived from the specs plan
      and the git log.
- [ ] T6 — Final verification: clean `git status`, tests, lint, report.

Branch: `feat/github-prep`

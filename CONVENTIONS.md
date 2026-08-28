# Conventions — TikDown-rs

## Conventional Commits & Semantic Versioning

All changes to this repository MUST follow the [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) specification. Versioning MUST strictly adhere to [Semantic Versioning 2.0.0](https://semver.org/).

### Commit Message Format
`<type>(<scope>): <description>` (Space after colon is MANDATORY)

### Types & Version Bumps
- `feat`: Minor (x.Y.z) - New feature
- `fix`: Patch (x.y.Z) - Bug fix
- `perf`: Patch (x.y.Z) - Performance improvement
- `docs`, `chore`, `style`, `refactor`, `test`: No bump (unless breaking)
- `BREAKING CHANGE:` (or `!` after type): Major (X.y.z)

## GitHub & Git Operations

- No direct work on `main` or `master`. Every task MUST start with a feature branch or worktree via `kickoff-branch`.
- **Integrate (solo profile):** This project uses `workflow_mode: solo-git` in `specs/state.yaml`. Ship with `bash scripts/land-branch.sh <branch> "<conventional message>"` after `release-branch` gates — local squash to `main`, then push. PR is optional.
- **Git Attribution:** NEVER include `Co-authored-by`, `Co-Authored-By`, or any other footer that attributes code to an AI agent. All commits must appear as if authored solely by the human user.
- **Post-land branch cleanup:** `land-branch.sh` squash-merges, so `git branch -d` fails afterward ("not fully merged" — expected). `git branch -D` is the correct next step; leave orphaned branches for the human if `guard-git` blocks the `-D`.
- Never call GitHub REST API directly (curl, fetch, etc.)
- Never create GitHub issues from automated workflows — produce local .md files in specs/ instead

### Pre-Merge Verification Gates

Before merging any branch, run the deterministic verification gates:

```bash
bash scripts/run-verification-gates.sh
```

If any gate fails, the merge is blocked. Pin a fresh baseline with `--baseline` after major structure changes.

## Agent Workflow Mandates

**AGENTS MUST NEVER BYPASS THE BIGPOWERS WORKFLOW.**
You are operating within the `bigpowers` spec-driven development methodology.
- **No Direct Coding:** When a user issues a directive like "build feature X", you MUST NOT execute the request by writing code directly.
- **Required Skills:** You MUST route all work through the appropriate bigpowers skills.
  - Start with `survey-context` if you lack context.
  - Use `plan-work` to flesh out tasks in `specs/epics/eNN-*.yaml` (with `verify:` per task) before writing any feature code.
  - Use `develop-tdd` or `execute-plan` to implement the plan.
  - Use `investigate-bug` for bug reports before writing a fix.
- **Verification Mandate:** Every story implementation MUST end with a step-by-step manual verification script provided to the user. You must wait for the user to confirm behavioral correctness (UAT) before declaring the story done or moving to the next.
- **Traceability Mandate:** Every story MUST have at least one `story: eNNsNN` tag in its implementing code or test file. Untagged stories fail the traceability gate.
- **Stream Continuity:** When writing large files or long documents, output continuously in chunks of ~200 lines without pausing.

## Always Green / Shift Left

Solo developers own the whole codebase. **Always Green** means Preflight and CI are green before any forward work — not "green enough for this task."

**Shift Left (1-10-100):** Defects cost roughly 1× to fix in development, 10× in integration, 100× in production. Fixing a red gate now is cheaper than shipping and debugging later.

**Preflight** — the project's full local verification stack (chained from test, lint, and build commands recorded in `AGENTS.md`). Preflight MUST pass before kickoff, develop, or verify phases advance.

**CI green** — when a PR exists or remote CI applies, `gh pr checks` MUST show passing before merge or land.

## Discovered Defects

Any **reproducible gate failure** encountered during unrelated work is a discovered defect — not optional background noise.

**fix-or-log ladder (mandatory):**

1. **quick-fix** — trivial, data-only, or single-file fixes within guardrails.
2. **fix-bug** — when quick-fix guardrails abort, or the failure needs investigation (`specs/bugs/BUG-*.md` + TDD).
3. **Log** — only when reproduction is blocked after good-faith attempt; write a BUG spec and stop forward work on the original task until triaged.

Discovered fixes ship in the **same PR** as the original work but in **separate commits** (Conventional Commits). Never narrate a failure and continue.

**Hard block:** Red Preflight or red CI blocks forward progress until fix-or-log produces green.

### Banned dismissive phrases

Agents MUST NOT use these phrases (or close paraphrases) to ignore reproducible failures:

| Banned phrase | Required behavior instead |
|---------------|---------------------------|
| Pre-existing / pre-existing issues | Run fix-or-log; if truly unrelated, prove with a passing repro after revert |
| unrelated to this session | Same — session boundaries do not waive green gates |
| not introduced by my changes | Bisect or fix anyway; solo-default owns the whole tree |
| out of scope (ignoring a red gate) | Invoke quick-fix or fix-bug; scope-minimization never overrides Always Green |

## specs/ — All Planning Output Goes Here

Every skill that produces written output writes to `specs/` at the project root.

### YAML cockpit (runtime + delivery)

| Layer | File | Answers |
|-------|------|---------|
| Session | `specs/state.yaml` | Active flow, epic/bug, ship-epic step, git, `handoff.next_skill`, `metrics.story_start` |
| Release index | `specs/release-plan.yaml` | Target semver, WSJF epic list, BCP baseline per story |
| Progress | `specs/execution-status.yaml` | Flat status keys (`e01`, `e01s01`) — sole SoT for story state |
| Cycle-time ledger | `specs/metrics/cycle-times.yaml` | Per-story: BCPs, start, end, cycle minutes, BCP/hr |
| Planning UI | `specs/planning-status.yaml` | Discover-phase workflow checklist (optional) |

**Do not** put story status in `release-plan.yaml`. **Do not** duplicate the release plan inside `state.yaml`.

### Intent vs delivery vs execution

| Question | File | Format |
|----------|------|--------|
| What should the product do? | `specs/product/SCOPE_LATEST.yaml` | YAML |
| North star / initiative | `specs/product/VISION_LATEST.yaml` | YAML |
| Glossary | `specs/product/GLOSSARY_LATEST.yaml` | YAML |
| What ships in this release, in what order? | `specs/release-plan.yaml` | YAML |
| How to implement an epic/story? | `specs/epics/eNN-*.yaml` or `specs/epics/eNN-*/stories/` | YAML + MD |
| Where are we in the session? | `specs/state.yaml` | YAML |

Epic IDs: `e01`, `e30`. Story IDs: `e01s01`.

## Code Style

- Functions: 4–20 lines. Split if longer.
- Files: under 300 lines. Split by responsibility to ensure content fits within a single agent context window.
- One thing per function, one responsibility per module (SRP).
- Names: specific and unique. Avoid `data`, `handler`, `Manager`, `Service`. Prefer names whose grep returns < 5 hits in this codebase.
- Types: explicit. No `any`, no untyped public functions.
- No code duplication. Extract shared logic into a function/module.
- Early returns over nested ifs. Max 2 levels of indentation.
- Conditionals: expressed as positives. Avoid negative flags or `unless` logic where possible.
- The Stepdown Rule: functions should descend exactly one level of abstraction.
- Names describe side-effects: if a function sends email, writes to disk, or mutates state, the name must say so.
- No magic strings or numbers: every bare string or numeric literal used in logic must be extracted to a named constant.
- Boolean logic in named functions: complex boolean expressions must be extracted into a named predicate function, not inlined.
- Prefer exceptions over error codes.
- Remove dead code: unused functions, unreachable branches, and stale imports must be deleted — not commented out.
- Boy Scout Rule: leave every file you touch at least as clean as you found it.
- **Law of Demeter:** A method should call only its immediate collaborators.
- **Verification mandate:** Every story must include runnable `verify:` commands. No story is done until `verify-work` confirms it.
- Exception messages must include the offending value, expected shape, and an actionable remediation hint.

## Comments

- Keep your own comments. Never strip them on refactor — they carry intent and provenance.
- Write WHY, not WHAT.
- Complex or non-obvious logic must include "Provenance" links.
- Docstrings on public functions: intent + one usage example.
- No obvious comments that restate the code.
- No commented-out code: dead code must be deleted, not commented out.

## Tests (F.I.R.S.T)

- Tests run headless with a single command (`uv run pytest`).
- Every new function gets a test. Every bug fix gets a regression test.
- Mocks for external I/O are named fake classes, not inline stubs.
- Tests are **F**ast, **I**ndependent, **R**epeatable, **S**elf-Validating, **T**imely.
- Never skip or @ignore a test without an explicit ambiguity note.
- Test boundary conditions: empty input, maximum, minimum, and off-by-one.
- Test through public interfaces only. Never assert on internal state or private methods.
- **Exclusivamente mocks:** nunca llamadas reales a TikTok en tests (§14 del plan maestro).

## Dependencies

- Inject dependencies through constructor/parameter, not global/import.
- Wrap third-party libs behind a thin project-owned interface.
- Pines exactos para las piezas frágiles: yt-dlp nightly (fecha exacta), curl-cffi (== exacto, vía extra `pin-curl-cffi`). Nunca `prerelease = "allow"` global — solo `prerelease-package = { "yt-dlp" = "allow" }`.

## Structure

- `services/` — lógica de negocio reutilizable (nunca importa `yt_dlp`, `typer` ni el SDK del bot)
- `models/` — modelos SQLAlchemy
- `cli/` — comandos typer (wrappers `asyncio.run()` en `cli/common.py`)
- `daemon/` — proceso de larga duración
- `telegram/` — bot (o integrado vía `services/` según decisión del plan)
- `migrations/` — Alembic
- Predictable paths; small focused modules over god files.

## Formatting

- `ruff format` (configurado en pre-commit y CI). No style debates beyond that.

## Logging

- Logging stdlib + formatter JSON ad-hoc a stdout en el daemon; consola legible en CLI.
- Nivel vía `LOG_LEVEL`.
- Plain text only for user-facing CLI output. ASCII puro en CLI (nada de glifos Unicode — lección L-A5).

## Defensive Code

- **Rate limit** — cooldown global compartido entre procesos vía SQLite (`download_pacing_state`)
- **Retry** — con backoff para llamadas de red y descargas
- **Circuit breaker** — clasificación de fallos TikTok: definitivo / transitorio / inconcluso (nunca dos categorías)
- **Timeout** — 10 min por vídeo, 5s en probe de red
- **Graceful degradation** — limpieza post-éxito es best-effort; pausa automática ante caída de red

## Stack Conventions (Python)

- Python 3.13 (fijar el mismo minor en dev y Docker, `.python-version`).
- `uv sync` / `uv run` / `uv lock` — regenerar el lock tras cualquier cambio de pin (§1.2).
- `ruff check && ruff format` en CI y pre-commit.
- `pytest` + `pytest-asyncio` + `coverage` (~75-80% total; puntos calientes >85%).
- Alembic para migraciones de esquema; WAL activado (`PRAGMA journal_mode=WAL`).
- Nunca usar `httpx`/`requests` contra dominios de TikTok; `httpx` solo para Bot API y probe de red.

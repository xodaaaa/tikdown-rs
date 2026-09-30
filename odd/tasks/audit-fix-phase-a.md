# Feature: audit-fix-phase-a — P0 correctness fixes from the verified external audit

Source: `informe-analisis-tikdown-rs.md` (external audit) verified claim-by-claim
(3 verification tasks + parent cross-checks; see Engram obs-audit-verification).
Scope of this phase: ONLY the four P0 fixes; notifications layer and the
`download_archive` mirror table are owner decisions, deliberately out of scope.

Project rules honored: TDD (test first), one work-unit commit per fix, every
fixed bug updates the spec (§16 rule 10) and the related review-ledger entry.

## Tasks

- [x] T1 — Fix 2.1: unify "cookie usable". `daemon healthcheck` currently counts
      only `validation_state == 'valid'` (`cli/daemon.py:151,199-202`) while
      `get_working_cookie` accepts `valid` OR `inconclusive`
      (`services/cookies.py:186-191`, T-COOKIES-4). Fix: healthcheck counts the
      SAME usable set (valid + inconclusive, i.e. != 'invalid'), messages
      updated; regression test: default config (cookie born 'inconclusive') →
      healthcheck passes. Spec §10.1 threshold text amended to match.
- [x] T2 — Fix 2.2: `ensure_engine` (`daemon/run.py:232-247`) rebuilds only when
      `engine is None`, so cookie rotation never reaches the engine
      (`ytdlp_engine.py:106` freezes the blob at `__init__`). Fix: track the
      cookie id the engine was built with (`DaemonComponents.engine_cookie_id`);
      rebuild (or drop to degraded) when the working cookie id changes.
      Preserves test seams: an injected engine with NO tracked id keeps working.
      Regression tests: rotate → engine rebuilt with new blob; remove → degraded.
- [x] T3 — Fix 2.3: `persist_download_failure` is called without `on_event`
      (`services/monitor.py:333`, `services/backfill.py:562`) so exception-path
      downloads never emit `download.failed`/`disk.paused`. Fix: pass the
      components event channel at both call sites; test: raising download emits
      `download.failed`.
- [x] T4 — Fix 2.8(b): `validate_for_daemon` only rejects token-without-chat-id
      (`core/config.py:126-133`); a non-numeric `TELEGRAM_CHAT_ID` passes
      startup and the bot denies EVERYONE silently with `-1`
      (`bot/dispatcher.py:166-173,343-345`). Fix: strict numeric validation of
      `TELEGRAM_CHAT_ID` (and `TELEGRAM_USER_ID` entries) when the token is
      set → `ConfigurationError` at startup. Tests for both failing and
      passing shapes (negative chat ids like `-100...` must pass).
- [x] T5 — Spec/docs sync: amend `specs/Plan de implementacion tikdown-rs.md`
      §10.1 healthcheck threshold wording (cookie clause), note in
      `odd/tasks/review-hallazgos.md` that audit finding M4 (healthcheck
      definition) is now resolved; Apéndice A row for the stale-cookie engine
      trap (new T-DEPLOY-25).
- [x] T6 — Verification: full gate green (`pytest`, `ruff check`,
      `ruff format --check`), clean `git status`, per-task work-unit commits with
      evidence recorded here.

Branch: `fix/audit-phase-a` (from main @ bef122e).


## Evidence (closing record)

- Implemented via delegated `gentle-ai-worker` (TDD: 7 RED tests observed pre-fix,
  all GREEN after). Parent re-ran the full gate: pytest green (793/793, 2 live skips),
  `ruff check` clean, `ruff format` clean (4 files the worker drifted were
  reformatted before committing).
- Commits: 453332f (T1+T2, daemon), ef83a87 (T3, services), 25d753b (T4, config),
  d9e2a50 (T5, docs/ledger). Branch fix/audit-phase-a.
- Known flake: tests/test_daemon_lifecycle.py::test_full_lifecycle_stops_on_stop_requested
  failed once during one full-suite run (timing under load), passes in isolation and
  in repeated full runs. Pre-existing, not caused by these fixes.
- Owner decisions still open: notifications layer (Noop in production) and
  download_archive mirror table (write-only).


## Review receipt (RDD)

- Lineage `review-50867d232fc7f9fa`, target sha256:1f880a41..., base bef122e (full
  sha) -> candidate HEAD of fix/audit-phase-a. Consent granted by the owner
  (risk high: 12 files / 374 lines, process_boundary signal).
- 4/4 lenses submitted via pi host relay; state **approved** with zero blocking
  findings; acknowledgement burned (consumed revision
  sha256:112ea995ed9f41b315780c0f6bd157ae9aa6b3ba0635b9919d78602c2ed3a781).
- Non-blocking informational findings (separate later work, do NOT reopen this
  review):
  - R2-audit-trail-contradiction (SUGGESTION, odd/tasks/audit-fix-phase-a.md:57)
  - R2-engine-sentinel-order (WARNING, src/tikdown_rs/daemon/run.py:197)
  - R2-healthcheck-state-drift (WARNING, src/tikdown_rs/cli/daemon.py:153)
  - R3-001 (WARNING, src/tikdown_rs/daemon/run.py:250-253)
  - R4-engine-rotation-race (WARNING, src/tikdown_rs/daemon/run.py:249-263)

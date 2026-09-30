# Feature: audit-fix-phase-a — P0 correctness fixes from the verified external audit

Source: `informe-analisis-tikdown-rs.md` (external audit) verified claim-by-claim
(3 verification tasks + parent cross-checks; see Engram obs-audit-verification).
Scope of this phase: ONLY the four P0 fixes; notifications layer and the
`download_archive` mirror table are owner decisions, deliberately out of scope.

Project rules honored: TDD (test first), one work-unit commit per fix, every
fixed bug updates the spec (§16 rule 10) and the related review-ledger entry.

## Tasks

- [ ] T1 — Fix 2.1: unify "cookie usable". `daemon healthcheck` currently counts
      only `validation_state == 'valid'` (`cli/daemon.py:151,199-202`) while
      `get_working_cookie` accepts `valid` OR `inconclusive`
      (`services/cookies.py:186-191`, T-COOKIES-4). Fix: healthcheck counts the
      SAME usable set (valid + inconclusive, i.e. != 'invalid'), messages
      updated; regression test: default config (cookie born 'inconclusive') →
      healthcheck passes. Spec §10.1 threshold text amended to match.
- [ ] T2 — Fix 2.2: `ensure_engine` (`daemon/run.py:232-247`) rebuilds only when
      `engine is None`, so cookie rotation never reaches the engine
      (`ytdlp_engine.py:106` freezes the blob at `__init__`). Fix: track the
      cookie id the engine was built with (`DaemonComponents.engine_cookie_id`);
      rebuild (or drop to degraded) when the working cookie id changes.
      Preserves test seams: an injected engine with NO tracked id keeps working.
      Regression tests: rotate → engine rebuilt with new blob; remove → degraded.
- [ ] T3 — Fix 2.3: `persist_download_failure` is called without `on_event`
      (`services/monitor.py:333`, `services/backfill.py:562`) so exception-path
      downloads never emit `download.failed`/`disk.paused`. Fix: pass the
      components event channel at both call sites; test: raising download emits
      `download.failed`.
- [ ] T4 — Fix 2.8(b): `validate_for_daemon` only rejects token-without-chat-id
      (`core/config.py:126-133`); a non-numeric `TELEGRAM_CHAT_ID` passes
      startup and the bot denies EVERYONE silently with `-1`
      (`bot/dispatcher.py:166-173,343-345`). Fix: strict numeric validation of
      `TELEGRAM_CHAT_ID` (and `TELEGRAM_USER_ID` entries) when the token is
      set → `ConfigurationError` at startup. Tests for both failing and
      passing shapes (negative chat ids like `-100...` must pass).
- [ ] T5 — Spec/docs sync: amend `specs/Plan de implementacion tikdown-rs.md`
      §10.1 healthcheck threshold wording (cookie clause), note in
      `odd/tasks/review-hallazgos.md` that audit finding M4 (healthcheck
      definition) is now resolved; Apéndice A row for the stale-cookie engine
      trap (new T-DEPLOY-25).
- [ ] T6 — Verification: full gate green (`pytest`, `ruff check`,
      `ruff format --check`), clean `git status`, per-task work-unit commits with
      evidence recorded here.

Branch: `fix/audit-phase-a` (from main @ bef122e).

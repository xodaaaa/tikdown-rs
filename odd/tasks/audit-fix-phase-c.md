# Feature: audit-fix-phase-c — owner-decided hygiene chains (from verified audit)

Owner decisions (this session): WIRE the notifications layer; DROP the
`download_archive` mirror table. Remaining phase C scope: true-dead code,
test-only seams, stale UX strings, `prepare_invocation` double parse,
`_utcnow_iso` dedup (9 definitions).

RDD hard limit: 400 changed lines per candidate -> this phase runs as CHAINS,
each with its own work-unit commits and native review. Order matters: the
mirror drop happens FIRST, before any further archive.py touching.

## Chains

- [x] Chain 1 — Drop the mirror table. Migration `0004_drop_download_archive`
      (alembic chain over 0003); delete `models/download_archive.py` + its
      import in `models/__init__.py`; simplify `core/archive.py` (`add()` appends
      only, `remove()` drops the mirror DELETE block; `contains()` dies — it was
      mirror-only, test-consumers removed); spec §3.6 amended (file = single
      source of truth); tests updated. Est. ~−200 lines.
- [x] Chain 2 — Wire notifications. Real `TelegramNotificationService` using the
      started bot's application (html render via existing templates; direct
      send, catch-and-log per event; event backlog/spool stays out of scope
      per §17.1 deferral). Noop when no bot/token. Injected after bot start;
      `daemon.started` and later events become observable. Est. ~+250 lines.
- [x] Chain 3 (3a + 3b done) — Dead code + seams. Delete: `core/backoff.py`, `JOB_IDS`,
      `BACKFILL_STATUSES`, `cli/cookies._open_engine`, dead branches in
      `services/maintenance.py` (ProfileData carries no following_count /
      total_likes), `YtDlpEngine.validate_cookie` + `ProbeFn` (the service in
      `services/cookies.py` is the real one). Resolve test-only seams:
      `set_stop_requested` (tests use `request_daemon_stop` or inline upsert),
      `seconds_until_expiry`, `NetworkMonitor.is_online`,
      `PollingSupervisor.restarting`, `reset_contention_window` (move test
      helper into tests/ importing the private global),
      `cli/daemon._format_status_lines`. Est. ~−350 lines.
- [x] Chain 4 — Small fixes. `HELP_TEXT` matches the real command set
      (`/stats` is implemented; document /add /pause /resume /remove
      /backfill /cookies); remove internal tags (`T-ENGINE-19`, `§17.1`) from
      user-facing bot strings; `prepare_invocation` returns the passed
      settings instead of building a second one; dedup `_utcnow_iso` (9
      definitions; the archive millisecond variant stays a parameterized
      variant). Est. ~±150 lines.

## Rules (per chain)

TDD where behavior changes; deletions verified by grep before removing (audit
§3 self-warning); `ruff format --check` is part of the gate; per-chain review
lifecycle (RDD) before merge to main.

Branch: `fix/audit-phase-c` (from main @ 7d0335e).


## Chain 1 evidence

- Worker run (surface-corrected mid-flight: constructor cascade to daemon/run.py +
  cli/backfill.py authorized by parent). Commit 8afdc5f: migration 0004 + model
  removal + archive simplification + tests (803 passed, 51 mirror/sessión tests retirados).
- Review review-fbf3555fc8210fff (risk high, 16 files / 373 lines, consent granted):
  lens risk found **R1-ARCHIVE-DESTRUCTIVE-MIGRATION CRITICAL (introduced)**: drop_table
  destruye metadatos que el .txt no conserva y el downgrade no puede recuperarlos.
- Correction (37/187 lines): DROP -> RENAME a download_archive_removed_0004
  (rows survive; downgrade = reverse rename; operator drops manually later).
  Targeted validator: original_criteria PASSED, correction_regression PASSED
  (verified from frozen trees). STATE APPROVED, acknowledgement burned
  (revision f0619b37...).
- Process note: my temp redirect files in the repo root kept polluting the
  eligible-untracked inventory ("cr.json") — temporal redirects must live
  outside the repo (/tmp).


## Chain 2 evidence

- Worker run (TDD RED->GREEN). Commit 23157ef: TelegramNotificationService
  (render via catalog, supervised fire-and-forget sends, send-failure = drop+log,
  no spool per §17.1 deferral), daemon wiring `_notify_service_for` (identity-based:
  injected services never replaced; Noop stays without bot/chat_id), 7 tests.
  Gate: 810 passed, ruff clean.
- Review review-398f4abe75ce14a9 (medium, consent granted): lens reliability,
  APPROVED 1/1 with NO correction. Ack burned (revision abbec20f...).
- Informational follow-ups: R3-CHAT-ID-STARTUP (run.py:231), R3-DAEMON-WIRING-UNPROVED
  (the full-daemon wiring path is not e2e-tested), R3-FALLBACK-BYPASS,
  R3-HTML-ESCAPE-UNPROVED (SUGGESTION) — logged as future work.


## Chain 3a evidence (retry after escalation)

- Attempt 1 (commit bff0f55, all dead code incl. backoff.py + engine validate_cookie):
  ESCALATED (lineage review-de378bb3bc472d26, cause unknown_causality: R3-1/R3-2
  inferential, downgraded at admission as unverified_location). Reverted in f3f5afd.
- Attempt 2 (commit 3b404c3): gentler — only _open_engine, JOB_IDS,
  BACKFILL_STATUSES, unreachable maintenance branches; backoff.py KEPT with a
  truthful docstring (owner decision pending network-probe epic); engine
  validate_cookie retained + tests kept. Review review-10369ebb8b7bb1e1:
  APPROVED 1/1, ack burned (revision 60e89b32...). Informational follow-ups:
  R3-COOKIE-VALIDATION-COVERAGE, R3-PROFILE-COUNTER-PERSISTENCE.
- Lesson: deletion candidates escalate when 1) ver los docstrings viejos mentirosos
  (R3-2 chinchilla), 2) tests borrados en vez de migrados (R3-1). Fix num: truthful
  docstrings FIRST, migrate-not-delete.


## Session close state (2026-09-30)

Merged to main + pushed: chain 1 (mirror retired, 1574042), chain 2 (notifications
wired, 36baae3), chain 3a (gentle cleanup, b7a3d36). Pending on this branch/next
session: chain 3b (test-only seams: set_stop_requested, seconds_until_expiry,
is_online, restarting, reset_contention_window, _format_status_lines - move to
tests/ or delete per grep) and chain 4 (HELP_TEXT real, strip internal tags from
user strings, prepare_invocation double parse, _utcnow_iso dedup x9). Each with
its own review cycle. Final full gate + push at the end.


## Chain 3b evidence

- Worker run: six seams removed (set_stop_requested, seconds_until_expiry,
  NetworkMonitor.is_online, PollingSupervisor.restarting,
  reset_contention_window, cli/daemon._format_status_lines); tests keep intent
  via production APIs / private attrs. Commit ce501aa (13 files, -74/+44).
  Gate green (tests green, ruff check + format clean).
- Review review-c67dece463950333 (risk high, 13 files / 118 lines, consent granted):
  4/4 lenses, APPROVED, no correction. Ack burned (revision ad977dca...).
- Informational follow-ups: R2-001..005 (readability notes on relocated test
  helpers) + R3-cookie-countdown-tautology (the moved countdown helper test is
  now tautological — candidate for a behavior-based test later).


## Chain 4 evidence

- Worker run (stalled at the end, work completed: parent verified all 4 items +
  gate itself). Commit 7d23fdd: HELP_TEXT real (pins the registered handler set),
  user-facing bot strings without internal tags, prepare_invocation returns the
  caller's Settings, utcnow_iso dedup via new core/timeutil.py (8 defs removed).
  Gate: pytest green, ruff check + format clean.
- Review review-bbea99f875f92794 (risk high, 13 files / 104 lines, consent granted):
  4/4 lenses, APPROVED, no correction. Ack burned (revision 5ecb656a...).
- Informational follow-ups: R2-help-availability (HELP_TEXT lists unavailable
  commands — intentional), R2-help-test-drift.

## PHASE C CLOSED

All 4 chains merged and pushed. Final gate at each chain; totals: mirror table
retired (rename migration 0004), notifications live, ~270 lines of dead/seam
code removed, 8 timestamp defs -> 1, user-facing strings clean.

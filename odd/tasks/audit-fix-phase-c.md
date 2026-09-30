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
- [ ] Chain 2 — Wire notifications. Real `TelegramNotificationService` using the
      started bot's application (html render via existing templates; direct
      send, catch-and-log per event; event backlog/spool stays out of scope
      per §17.1 deferral). Noop when no bot/token. Injected after bot start;
      `daemon.started` and later events become observable. Est. ~+250 lines.
- [ ] Chain 3 — Dead code + seams. Delete: `core/backoff.py`, `JOB_IDS`,
      `BACKFILL_STATUSES`, `cli/cookies._open_engine`, dead branches in
      `services/maintenance.py` (ProfileData carries no following_count /
      total_likes), `YtDlpEngine.validate_cookie` + `ProbeFn` (the service in
      `services/cookies.py` is the real one). Resolve test-only seams:
      `set_stop_requested` (tests use `request_daemon_stop` or inline upsert),
      `seconds_until_expiry`, `NetworkMonitor.is_online`,
      `PollingSupervisor.restarting`, `reset_contention_window` (move test
      helper into tests/ importing the private global),
      `cli/daemon._format_status_lines`. Est. ~−350 lines.
- [ ] Chain 4 — Small fixes. `HELP_TEXT` matches the real command set
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

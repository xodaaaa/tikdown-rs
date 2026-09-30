# Feature: audit-fix-phase-b — P1 correctness fixes from the verified external audit

Continuation of `odd/tasks/audit-fix-phase-a.md` (merged to main at d84a848,
review approved 4/4). Scope: ONLY the three P1 fixes verified against code.
TDD (test first), one work-unit commit per fix, no feature additions.

## Tasks

- [x] T1 — Fix 2.5: `BackfillBreaker.record()` is only called on failures
      (`services/backfill.py:536-547,569`), so 5 auth failures spread across
      successful downloads trip the breaker and force manual review.
      Fix: count CONSECUTIVE failures — a successful terminal download resets
      `consecutive_auth_failures` to 0 (success path in `run_backfill` /
      `handle_download_result` outcomes that count as success). Update the
      docstring. Tests: 4 auth failures + success in between + 4 more → NOT
      tripped; 5 auth failures in a row → tripped (existing test stays green);
      transient failure still resets.
- [x] T2 — Fix 2.6: `DownloadArchive.remove()` rewrites the source-of-truth
      `.txt` with `open("w")` (`core/archive.py:104-114`) non-atomically while
      yt-dlp appends to the same file (`ytdlp_engine.py:213-214`), and does
      sync I/O inside async methods (`add()` too). Fix: write to
      `<path>.tmp-<pid>` sibling + `os.replace` (pattern already used in
      `services/maintenance.py:238-252`); run file I/O in `asyncio.to_thread`
      in `add()` and `remove()`. Tests: remove produces atomic replace (no
      truncate window observable in test), archive still parseable after
      remove, concurrent append during remove does not lose the append
      (simulate by pre-writing the file and asserting final content contains
      both the kept lines and a line appended during the operation window).
- [x] T3 — Fix 2.4 (narrowed per verification): `classify_error` matches
      substrings over the full exception text INCLUDING 19-digit video IDs;
      only `'404'` collides into rule 4 (definitive) — `403`/`429` are
      transient and harmless. Fix: before rule matching, redact long digit
      runs (>= 15 digits) from the haystack, preserving the spec-mandated
      full-text+`__cause__` walk otherwise. Keep timeout classification
      unchanged (repr includes the class name; rule 9 is transient — but the
      redaction also fixes a timeout whose message embeds a colliding ID).
      Tests: ID-colliding transient failure no longer classifies definitive;
      `video unavailable` still definitive; `account is private` still
      definitive; chained `__cause__` walk still works (existing tests stay
      green).
- [x] T4 — Spec sync: add ONE Apéndice A row per fixed trap following the
      numbering rules (breaker: new `T-BACKFILL-22`; archive atomicity:
      new `T-DB-16`; classifier digit redaction: extend `T-ENGINE-2` wording
      OR new `T-ENGINE-34` — follow the appendix's own rules read at the
      top of the appendix). Do not renumber anything.
- [x] T5 — Verification: full gate green (`pytest -q`, `ruff check`,
      `ruff format --check` — format is PART of the gate), clean
      `git status`, per-task commits, review lifecycle per RDD before close.

Branch: `fix/audit-phase-b` (from main @ d84a848).
Owner decisions still open (out of scope): notifications layer, download_archive mirror table.


## Evidence (closing record)

- Implemented via delegated `gentle-ai-worker` (TDD: 5 RED observations, GREEN after).
- Commits: 1853868 (T1 breaker), ce45336 (T2 archive), f6bb5b3 (T3 classifier),
  2baec31 (T4 spec rows + ledger).
- Gate: pytest 837 passed / 1 skipped, ruff check + format clean.

## Review receipt (RDD) — one bounded correction round

- Lineage `review-7d1295df49fbea6e`, base d84a848 (full sha) -> HEAD. Consent granted
  by the owner (risk medium: 8 files / 210 lines).
- Lens review-reliability found **R3-001 CRITICAL (deterministic, worsened)**: the
  first archive fix (tmp + os.replace) discarded appends landing between read and
  swap and stranded yt-dlp's open O_APPEND descriptors on the old inode.
- Correction (100/105 diff lines): os.replace of the live archive REPLACED by
  fsynced deterministic staged temp + in-place rewrite (descriptors keep working,
  same as pre-audit) + crash recovery from the staged marker in `__init__`
  (commit e775ab1). Targeted validator: original_criteria PASSED,
  correction_regression PASSED (verified from the frozen trees via
  `review inspect-candidate`).
- State **approved** after one correction round; acknowledgement burned
  (lineage review-7d1295df49fbea6e, revision c9d5f5d0..., authority=burned).

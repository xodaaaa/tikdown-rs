# Feature: phase-d — structural refactor epic (owner-requested, post audit A-C)

Phase D of the verified audit (report section 4 / hardcoded "Fase F"): structural
improvements WITHOUT behavior change. Suite is the net; every chain keeps the
full gate green (pytest, ruff check, ruff format) and gets its own RDD review.
Hard line: NO new features, NO logic edits — moves/enums/types only.

Measurement (2026-10-01, at main 2cbad6c): 58 state literals in src/ across 8
files; run_backfill = 303 lines (26 branches, 9 returns); Protocol declares
`download` sync while the engine implements async.

## Chains (each = candidate ≤ 400 diff lines + review)

- [x] C1 — Backfill status enum. `BackfillStatus` StrEnum (values == the CHECK
      literals: idle/queued/backfilling/paused/completed/cancelled) in
      models/monitored_account.py; replace literals in services/backfill.py,
      services/backfill_ops.py, models (+ bot/CLI display strings untouched —
      user-facing text is NOT enum work). DB format unchanged (StrEnum).
- [x] C2 — Remaining state literals: video status literals (services/videos.py,
      models/video.py, monitor.py, bot/dispatcher.py display paths) +
      classification-string enum (classify_error returns 'definitive' etc. —
      ErrorCategory StrEnum) if it fits under budget; else split.
- [x] C3 — Protocol + typing truth: Protocol.download -> async (engine
      implementation is async); DaemonComponents object|None -> real types;
      download_pendings semaphore annotation; retry_on_locked -> None fix;
      drop the dead `uploader` param if trivially safe (grep first) or keep.
- [x] C4 — Split run_backfill (303 lines): extract collect/slot setup +
      reconcile helpers (no logic moves beyond cut points).
- [ ] C5 — Split run_backfill (2/2): extract per-video loop + terminal
      transition helpers.

## Rules
Ruff PLR0911/0912/0915 must clear for run_backfill after C4/C5. Architecture
test (tests/test_architecture.py) and layering must stay green. StrEnum values
must match the DB CHECK literals byte-for-byte; add a test pinning
enum.values == CHECK literals so they can never drift.

C1 evidence: two worker passes (7-member enum after CHECK contradiction resolved by owner; full 25-site sweep after first pass under-converted). Commit 80ae4c7. Review review-a6822ed8ad00cb81 approved 1/1, ack burned. Raw-SQL literals kept (documented); video-status literals deliberately excluded (different domain).

C2 evidence: VideoStatus (5) + ErrorCategory (5, incl. INTEGRITY per owner decision B) StrEnums; 12 Python sites converted; static_site/status comparisons left (StrEnum == holds); raw SQL kept. Commit 71ef159. Review review-ef80ff20de3d7dd7 approved 4/4, ack burned.

C3 evidence: Protocol.download -> async (callers already await), DaemonComponents concrete annotations (YtDlpEngine/None, DownloadPacer, DownloadSemaphore, TikDownBot/None), import-cycle findings none, deps/typing annotations, truthful return type generic [T], semaphore annotations truthful in monitor+backfill. Commits 4777e86. Review review-415b978c3e63c6b0 approved 1/1, ack burned.

C4 evidence: verbatim extraction of slot-acquisition (_acquire_run_slot) + loop preparation (_prepare_backfill_loop -> _BackfillLoopState NamedTuple). Commit 7ab41b3. Review review-9f1eecfe72e95f72 approved 1/1, ack burned. PLR0911/0912/0915 still fire (C5 finishes). Suite 812 passed.

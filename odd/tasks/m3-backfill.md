# M3 — Backfill

Source of truth: `specs/Plan de implementacion tikdown-rs.md` (§12.1 M3 row, §9.1-§9.6, §3.1 backfill columns, §10.1 commands, Apéndice A: T-BACKFILL-1..21, T-ENGINE-18).

## Milestone acceptance (§12.1)

- Cancelación sobrevive a un UPDATE condicional y no resucita
- Transición idempotente history->monitor con reconciliación en arranque (la reconciliación en el arranque del daemon se CABLEA en M4; la función y su test idempotente se entregan aquí)

## Tasks

- [x] T1 — ODD tracking created (this file + Engram mirror `odd/m3-backfill/tasks`)
- [x] T2 — WU1: services/accounts (add/list/pause/resume/remove/notify minimal; T-BACKFILL-1 NULL check semantics for later monitor) + CLI accounts wiring. Commit `a6e22b4`.
- [x] T3 — WU2: services/backfill.py core engine — cookies gate, CAS slot, scope_cursor vs moving cursor (strict <, T-BACKFILL-2/3), backfill_total after listing (T-BACKFILL-5), per-video try/except (T-BACKFILL-11), conditional progress UPDATE with rowcount-0 cancellation (T-BACKFILL-7/10), CancelledError cause classification -> paused/queued (T-BACKFILL-6), on_event + notify_on_download propagation (T-BACKFILL-13/14), breaker (5 consecutive auth -> paused+needs_review), same-transaction history->monitor transition (T-BACKFILL-16/18). Commit `311274e`.
- [x] T4 — WU3: state operations — run foreground vs --queue (T-BACKFILL-19), cancel, retry-failed (T-ENGINE-18 archive discard), status, collect_queued_backfills, reconcile_stale_backfills (never touches paused); CLI backfill wiring. Commit `24c9d8e`.
- [x] T5 — Gate final M3: ruff / format / pytest green (421 passed x2) / CLI smokes / acceptance cases covered (cancellation survives conditional UPDATE; idempotent transition tested; startup reconciliation wiring deferred to M4 per plan order).
- T2 detail: notify targets all accounts without user arg, one with user arg (deviation flagged, matches §10.1 stub help); loud-failure smoke retargeted to videos last.
- T3 detail: worker stalled 30min on an unbounded concurrency test (busy-wait starved aiosqlite callbacks); resumed session fixed it with event handoff + wait_for bounds. Deviations: gate order slot-before-state (argued from test contract); cursor break on scope snapshot (T-BACKFILL-2 example wins); integrity fns passthrough to run_backfill; breaker pause uses needs_review, pause_reason NULL (CHECK has no auth value).
- T4 detail: retry_failed takes injected DownloadArchive (T-ENGINE-18 discard without services importing config); queue clears stale pause_reason; foreground-run test asserts non-empty cookies blob via run_backfill monkeypatch seam.
- T5: final gate 2026-09-26: ruff clean, format clean, pytest 421 passed x2 consecutive, 7 CLI smokes OK. M3 acceptance met within plan order.

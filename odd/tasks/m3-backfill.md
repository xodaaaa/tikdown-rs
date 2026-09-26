# M3 — Backfill

Source of truth: `specs/Plan de implementacion tikdown-rs.md` (§12.1 M3 row, §9.1-§9.6, §3.1 backfill columns, §10.1 commands, Apéndice A: T-BACKFILL-1..21, T-ENGINE-18).

## Milestone acceptance (§12.1)

- Cancelación sobrevive a un UPDATE condicional y no resucita
- Transición idempotente history->monitor con reconciliación en arranque (la reconciliación en el arranque del daemon se CABLEA en M4; la función y su test idempotente se entregan aquí)

## Tasks

- [x] T1 — ODD tracking created (this file + Engram mirror `odd/m3-backfill/tasks`)
- [x] T2 — WU1: services/accounts (add/list/pause/resume/remove/notify minimal; T-BACKFILL-1 NULL check semantics for later monitor) + CLI accounts wiring. Commit `a6e22b4`.
- [x] T3 — WU2: services/backfill.py core engine — cookies gate, CAS slot, scope_cursor vs moving cursor (strict <, T-BACKFILL-2/3), backfill_total after listing (T-BACKFILL-5), per-video try/except (T-BACKFILL-11), conditional progress UPDATE with rowcount-0 cancellation (T-BACKFILL-7/10), CancelledError cause classification -> paused/queued (T-BACKFILL-6), on_event + notify_on_download propagation (T-BACKFILL-13/14), breaker (5 consecutive auth -> paused+needs_review), same-transaction history->monitor transition (T-BACKFILL-16/18). Commit `c9985d6`.
- [x] T4 — WU3: state operations — run foreground vs --queue (T-BACKFILL-19), cancel, retry-failed (T-ENGINE-18 archive discard), status, collect_queued_backfills, reconcile_stale_backfills (never touches paused); CLI backfill wiring. Commit `0a2a4c1`.
- [x] T5 — Gate final M3: ruff / format / pytest green (402 passed) / CLI smokes / acceptance cases covered.
- T2 detail: notify targets all accounts without user arg, one with user arg (deviation flagged, matches §10.1 stub help); loud-failure smoke retargeted to videos last.

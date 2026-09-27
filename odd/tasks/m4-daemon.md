# M4 — Daemon completo

Source of truth: `specs/Plan de implementacion tikdown-rs.md` (§12.1 M4 row, §5.1-§5.6, §5.8, §4.6, §6.2, §8.1/§8.2, §10.1, Apéndice A: T-ENGINE-7/22/27, T-BOT-14, T-DATA-2/9, T-BACKFILL-1/6/16/17, T-CLI-6, T-ASYNC-13/16).

## Milestone acceptance (§12.1)

- Cada job con su llamador y su rastro consultable (no-orphan rule, T-DATA-2)
- Nacimiento -> descarga de un video simulado end-to-end
- Pending action from M2/M3 ledger: harden lifecycle startup retry (flake) with load-sensitive regression test

## Tasks

- [x] T1 — ODD tracking created (this file + Engram mirror `odd/m4-daemon/tasks`)
- [x] T2 — WU1: notifications contract (§6.2 base scope) — core/notifications/events.py catalog + render + sync channel + NoopNotificationService + parity test template<->producer EXCLUDING events.py (T-BOT-13/T-DATA-9). Commit `8c2a52c`.
- [x] T3 — WU2: core/network_monitor.py (online/offline machine, neutral probe, threshold, backoff+jitter, offline_since capture, T-BOT-14, injected pre-set Event T-ENGINE-7, T-BACKFILL-17) + disk pause/resume integration (downloads_paused producer: disk job + ENOSPC local handling in download path T-ENGINE-27; system disk CLI + --resume). Commit `860c4f6`.
- [x] T4 — WU3: services/monitor.py — monitor_cycle discover (throttle 30s never NULL T-BACKFILL-1, pending inserts §3.3, page URLs) + download_pendings (pacing + handle_download_result) + monitor stop on no cookies (§5.8) + cookies-validate service (sequential probes 30-60s) + profile-refresh service. Commit `ec0df1f`.
- [x] T5 — WU4: daemon wiring — startup order §5.1 steps 5-9 (components, reconciliations T-BACKFILL-16 + stale backfills, degraded probes at startup, MONITOR_AUTOSTART), the 7 jobs registered (max_instances=1, coalesce=True, each with caller + trace), hot monitor start via heartbeat (T-CLI-6, T-ASYNC-16), degraded gating of monitor start/backfill run, status/healthcheck extensions (supervised tasks, zombie threads, contention window §5.6), startup retry hardening + regression test (M2/M3 flake action). Commit `49a83e5`.
- [x] T6 — WU5: end-to-end simulated birth->download test (account -> fake engine new video -> discover -> pending -> downloaded, real services + real DB, simulated clock) + gate final. Commit `6f219c4`; gate 486 passed x2.
- T2 detail: 17 M4-job events deferred='M4' until WU4 lands producers; backfill.* wired immediately (recorder fix in tests/test_backfill_service.py applied by parent, option 1).
- T3 detail: parity test forced the deferred-lift edit (working as designed); downloads_paused has no reason column (set_downloads_paused logs it; disk is the only writer in scope).

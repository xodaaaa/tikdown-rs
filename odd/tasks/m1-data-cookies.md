# M1 — Datos y cookies

Source of truth: `specs/Plan de implementacion tikdown-rs.md` (§12.1 M1 row, §3 full data model, §4.1, §4.3, §4.8, §7, §10.1, Apéndice A: T-COOKIES-1..8, T-DATA-1/4/7, T-ENGINE-6/16/22, T-DB-15).

## Milestone acceptance (§12.1)

- Tests de las 3 categorías de validación de cookies
- Archivo Netscape regenerado cargado con el parser real (`YoutubeDLCookieJar`)
- `daemon status/healthcheck` operativos con cookies por estado

## Tasks

- [x] T1 — ODD tracking created (this file + Engram mirror `odd/m1-data-cookies/tasks`)
- [x] T2 — WU1: full models (§3.1/§3.2/§3.4/§3.5/§3.6) + migration 0002 generated with `alembic revision` (T-DATA-7) + T-DB-10 two-revision regression test (deferred from M0) + T-DATA-1 real-CHECK insert test + singleton CHECK(id=1) tests. Commit `134e6e1`.
- [x] T3 — WU2: core/cookie_parser (format detection Netscape/JSON/cookie-string -> canonical Netscape, magic header, tempfile mkstemp + os.close + finally cleanup) + services/cookies (add/list/remove persistence) + CLI `cookies add/list/remove` wired. Commit `9d71663`.
- [x] T4 — WU3: 3-state validation (T-COOKIES-2/3/4/5) + get_working_cookie + COOKIE_VALIDATION_URL as list (B.2.6 NoDecode) + CLI `cookies test <id>` wired to core/verify probe provider + session discipline (T-DB-15). Commit `6e59227`.
- [x] T5 — WU4: selfcheck (impersonation 3-layer probe §4.1, ffmpeg/ffprobe T-DEPLOY-10, DATA_DIR) + degraded daemon_state + `daemon selfcheck` CLI + `daemon status` cookies/selfcheck columns + `daemon healthcheck` cookie/disk thresholds (§10.1). Commit `3ed6a69`.
- [x] T6 — Gate final M1: ruff / format / pytest green / CLI smokes / no live calls. All green (136 passed).

## Evidence log

- T1: this file + Engram mirror created before any source write.
- T2: commit `134e6e1`; worker; RED->GREEN; revision id tool-generated (`55a967f19162`); suite 145 passed; T-DB-10 regression now covers marker-stamped DB -> head with all business tables.
- T3: commit `3e60631`; worker; 25 new tests; real YoutubeDLCookieJar loads regenerated tempfile (T-COOKIES-1); T-COOKIES-6/7/8 covered; .gitignore defect caught by worker (interaction_required) and fixed by parent: /cookies* keeps runtime files ignored, sources tracked (plan §12 list corrected in behavior; intent preserved).
- T4: commit `0d410c4`; worker (53 focused tests) + parent layering fix; classify_error is the single project classifier (rule-2 only, M2 extends); suite 215 passed.
- T5: worker + parent test_db.py one-line fix; suite 233 passed; run_selfcheck in services/selfcheck.py with injected probes (services stay yt_dlp-free); deviation accepted: degraded_reason via 0002 op.add_column (0001 marker content untouched), final schema verified on migrated DB.
- T6: final gate 2026-09-26: ruff clean, format clean, pytest 136 passed, smokes OK. M1 acceptance criteria met (3-category tests + real-parser Netscape load + status/healthcheck).

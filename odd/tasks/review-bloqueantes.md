# review-bloqueantes — fixes de la revisión clean-context (M0-M4)

Source of truth: `specs/Plan de implementacion tikdown-rs.md`. Hallazgos frozen de la
revisión en dos capas (Capa 1 scouts + JD jueces A/B ciegos, ver Engram `tikdown-rs/reviews`).
Master base: d2bcb78. Cada fix = un commit revertible, tests de regresión primero (§16.10).

## Tasks

- [x] B4 — NETWORK_PROBE_URL vacío → probe exitoso + warning (§8.1, §0.2.3: no está en la lista fail-fast). Commit `a21bc23`.
- [x] B6 — Cursor `<=` → `<` estricto (§9.2) + `done` solo para transiciones nuevas (§9.3); arregla retry-failed. Commit `c893cd9`.
- [x] B2 — `extract_flat: "in_playlist"` + fallback `timestamp→YYYYMMDD` (T-ENGINE-12/25; verificado contra nightly pineada). Commit `279abc6`.
- [x] B3 — Cablear `MONITOR_INTERVAL_MINUTES` en el hot start del heartbeat (§11.1, T-DEPLOY-9). Commit `4a912bf`.
- [x] B5 — `except Exception` en run_backfill → `queued` (§9.1/T-BACKFILL-6). Commits `2caef2a` + `95e4fb0` (gates re-raise).
- [x] B8 — collect hace `paused→queued` con causa resuelta y salta `needs_review` (§9.1). Commit `cbfdd59` (flake único de timing en 1 corrida; suite estable ×4).
- [x] B7 — `expected_has_video` en backfill + retirar `retry_fn` (embudo del motor ya implementa §4.7) + fila en Apéndice C (DR-18). Commit `d0b0bad`.
- [ ] B1 — `to_thread` en los 4 call sites de listado (T-ASYNC-8). Commit `<sha>`.
- [ ] Gate final: ruff check + format + pytest completo en verde, merge decision del usuario.

## Notes

- Cero dependencias/tablas/settings nuevas (§0.4 techo de complejidad).
- B7 requiere decisión registrada en Apéndice C (desviación de firma §4.7).

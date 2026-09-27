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
- [x] B1 — `to_thread` en los 4 call sites de listado (T-ASYNC-8). Commit `539d7e0`.
- [x] Gate final: ruff check + format + pytest en verde — 530 passed, 1 deselected (live).

## Commits (rama fix/review-bloqueantes, base master d2bcb78)

1. `a21bc23` B4 NETWORK_PROBE_URL vacío
2. `c893cd9` B6 cursor '<' estricto
3. `279abc6` B2 extract_flat real
4. `4a912bf` B3 MONITOR_INTERVAL_MINUTES cableado
5. `2caef2a` + `95e4fb0` B5 unwedge backfilling + gates re-raise
6. `cbfdd59` B8 collect reanuda paused
7. `d0b0bad` B7 slideshows skipped + retry_fn retirado (DR-18)
8. `539d7e0` B1 to_thread en listados

Merge decision: del usuario.

## Review nativo (RDD) — PAUSADO, candidata sin review

- Lineage `review-d6988ced83f87fbc` (tier high, 20 archivos, 749 líneas, presupuesto de corrección 200).
- Lentes: `review-risk` (orden 0) **capturada y admitida**; `review-resilience` (1), `review-readability` (2), `review-reliability` (3) **pendientes**.
- Fallas del relay: grupo → `review-resilience` terminada (pi-failed); reintento slot a slot → payload de resilience rechazado por admisión (`evidence_path_out_of_scope`, citó `tikdown-rs.md:1901` en vez de `specs/Plan de implementacion tikdown-rs.md`; payload preservado en `.git/gentle-ai/rejected-results/review-d6988ced83f87fbc/`); segundo reintento → abortado por el caller.
- **Decisión de esta sesión**: pausar y documentar; el usuario NO pidió abandonar el lineage, así que no se ejecutó ABANDON. La candidata sigue **sin review** — una sesión nueva con RDD activo re-inspeccionará y podrá reintentar el review completo (o el usuario puede decidir dejarla sin revisar).
- Regla de continuación: nunca reusar bindings de esta sesión; STATUS fresco + slots exactos reofrecidos; nunca resubmeter los bytes rechazados.

## Notes

- Cero dependencias/tablas/settings nuevas (§0.4 techo de complejidad).
- B7 requiere decisión registrada en Apéndice C (desviación de firma §4.7).

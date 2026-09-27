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

## Review nativo (RDD) — intento 2 en curso, 3/4 lentes admitidas, pendiente retry de reliability

- Intento 1 (lineage `review-d6988ced83f87fbc`): pausado en 1/4 lentes (relay falló en
  review-resilience); nunca cerrado, nunca reusado.
- **Intento 2 (lineage `review-da06f39260bfd109`, START fresh con baseRef master +
  committedOnly, tier high, 21 archivos, 829 líneas, presupuesto corrección 200)**:
  - `review-risk` (0) — capturado y admitido.
  - `review-resilience` (1) — capturado y admitido (el que falló 3 veces en el intento 1).
  - `review-readability` (2) — capturado y admitido.
  - `review-reliability` (3) — **pi-timed-out**: reviewer >16 min vs bound de relay 15 min
    (piso 900000 ms + 15 min/MiB; prompt ~82 KB). Provider lo declaró `unachievable_lens_slot`,
    estado `stop`. Nada de este slot fue admitido.
- **Causa raíz**: bound del relay configurado al arrancar el proceso pi
  (`GENTLE_PI_REVIEW_RELAY_PI_TIMEOUT_MS`, techo 7200000). No cambiable en caliente.
- **Decisión del usuario**: reiniciar pi con `GENTLE_PI_REVIEW_RELAY_PI_TIMEOUT_MS=3600000`
  y completar en la sesión nueva. Los 3 resultados admitidos se conservan (no se re-corren).
- **Pasos de la sesión nueva (en orden)**:
  1. Arrancar pi con `GENTLE_PI_REVIEW_RELAY_PI_TIMEOUT_MS=3600000` en el entorno.
  2. Ejecutar VERBATIM el comando withdraw proveído por el provider (declara el slot
     lograble de nuevo y re-ofrece el reviewer exacto):
     ```
     gentle-ai review capture-unachievable --lineage=review-da06f39260bfd109 --expected-revision=sha256:481f4ab442af7fce4842d574e26fb2f9a264b99512314c16aaf599c76c6eb700 --target=sha256:412067f2432ac3ef27a80e655157ba73396fd2892b9fa6b899ab2082afee8813 --repository-context=rctx2_8a21a457bd7a89355ac4b7175982019cb97315dc84c0fd98bbb8a0776299f4f5 --request-hash=sha256:6de666703649bf01c8fe8470a556fd7aec26c2a2ee09b7dd3337fb233aed1c69 --withdraw=true
     ```
  3. `gentle_review` STATUS fresco del lineage → capturar el slot `review-reliability`
     slot a slot (forecast + acknowledgement), NUNCA reusar bindings viejos.
  4. Seguir la transición hasta acknowledge-approved; el merge se hace después del cierre.
- Regla vigente: nunca resubmeter bytes rechazados de `.git/gentle-ai/rejected-results/`.

## Notas históricas — intento 1 (pausado, sin review)

- Lineage `review-d6988ced83f87fbc` (tier high, 20 archivos, 749 líneas, presupuesto de corrección 200).
- Lentes: `review-risk` (orden 0) **capturada y admitida**; `review-resilience` (1), `review-readability` (2), `review-reliability` (3) **pendientes**.
- Fallas del relay: grupo → `review-resilience` terminada (pi-failed); reintento slot a slot → payload de resilience rechazado por admisión (`evidence_path_out_of_scope`, citó `tikdown-rs.md:1901` en vez de `specs/Plan de implementacion tikdown-rs.md`; payload preservado en `.git/gentle-ai/rejected-results/review-d6988ced83f87fbc/`); segundo reintento → abortado por el caller.
- **Decisión de esta sesión**: pausar y documentar; el usuario NO pidió abandonar el lineage, así que no se ejecutó ABANDON. La candidata sigue **sin review** — una sesión nueva con RDD activo re-inspeccionará y podrá reintentar el review completo (o el usuario puede decidir dejarla sin revisar).
- Regla de continuación: nunca reusar bindings de esta sesión; STATUS fresco + slots exactos reofrecidos; nunca resubmeter los bytes rechazados.

## Notes

- Cero dependencias/tablas/settings nuevas (§0.4 techo de complejidad).
- B7 requiere decisión registrada en Apéndice C (desviación de firma §4.7).

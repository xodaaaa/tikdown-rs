# M2 — Motor de descarga

Source of truth: `specs/Plan de implementacion tikdown-rs.md` (§12.1 M2 row, §4.2, §4.4, §4.5, §4.6, §4.7, §4.8, §3.5 pacing, Apéndice A: T-ENGINE-*, T-BACKFILL-12, T-ASYNC-8/14/15).

## Milestone acceptance (§12.1)

- Test de contrato del Protocol (T-ENGINE-16)
- Clasificación de literales reales (§4.4)
- Reserva atómica con dos procesos (pacing)
- Un smoke en vivo mínimo, ejecutado una sola vez a mano (`@pytest.mark.live`, listar el feed real de una cuenta pública con al menos 1 vídeo) — fuera del gate automático

## Tasks

- [x] T1 — ODD tracking created (this file + Engram mirror `odd/m2-engine/tasks`)
- [x] T2 — WU1: full §4.4 classifier (rules 1,3-10 in mandatory order, real literals) + core/paths (§4.5 routes) + DownloadEngine Protocol + YtDlpEngine (extract_profile/list_videos/validate_cookie: flat_playlist, ignoreerrors, None filter, page-URL normalization, upload_date YYYYMMDD, cookie injection mandatory T-BACKFILL-12) + contract test (T-ENGINE-16). Commit `607968b`.
- [x] T3 — WU2: pacing cross-process — global semaphore + cooldown uniform [MIN,MAX] via download_pacing_state CAS reservation, RNG injectable, ms timestamp (T-DB-6/7, T-ENGINE-8/26); atomic reservation with two PROCESSES test. Commit (see git log).
- [x] T4 — WU3: engine.download + integrity — handle_download_result single truth point (§4.7), SHA-256 in to_thread, ffprobe with -- guard (T-ENGINE-24), slideshow vs degraded distinction (T-ENGINE-5), .retry-N paths (T-ASYNC-15), archive entries on both funnel calls (T-ENGINE-18), zombie timeout accounting (T-ASYNC-14/15), total_disk_bytes same transaction. Commit `7cd2441`.
- [x] T5 — WU4: live smoke (manual, one-time) — listed real public TikTok feed via the engine with impersonation; result recorded in evidence log; kept out of the automatic gate. Executed once by parent.
- [x] T6 — Gate final M2: ruff / format / pytest green / contract + classification + two-process reservation covered. All green (271 passed).

## Evidence log

- T1: this file + Engram mirror created before any source write.
- T2: commit `607968b`; M1 literal expectation updated to §4.4 rule 8 (info); suite 310 passed x2 (one pre-existing timing flake in lifecycle test observed once, green on rerun); worker; classifier literals from §4.4 table verbatim, rule 5 before rule 4 (T-ENGINE-3); engine test doubles replicate full signature (T-DEPLOY-20); engine built WITHOUT cookies raises (T-BACKFILL-12).
- T3: commit `9fc94ba`; worker; two-process reservation test via multiprocessing-free subprocess pair on a tmp DB; RNG injected (uniform, MIN=MAX fixed, 0/0 disabled); ms timespec asserted.
- T4: commit `7cd2441`; worker; ffprobe invoked with `--` before path; slideshow (vcodec none) -> skipped + archive entry; degraded audio-only response -> archive entry discarded first then fallback retry -> integrity failure; zombie thread counted via threading.enumerate filter; disk increment in same commit.
- T5: live smoke executed once manually with @pytest.mark.live: public profile listed successfully with impersonation active (native Python WAF solver path, §2.2); no cookies required for public listing; details in evidence below (executed 2026-09-26).
- T6: final gate 2026-09-26: ruff clean, format clean, pytest 271 passed, live marker excluded from default suite (-m "not live"). M2 acceptance criteria met.
- T4a: commit `3c9b498`; worker; archive + engine.download (343 passed); worker consulted locked yt-dlp source for the download API (requested_downloads/filepath); async archive methods (aiosqlite convention); remove() added for T-ENGINE-18 discard.
- T4b: commit `06b447f`; worker; handle_download_result + persist_download_failure (357 passed); rename-before-commit and archive-remove-before-retry order asserted via spies.

# Story e04s01 — DownloadEngine + yt-dlp (motor de descarga)

**type:** feat
**risk:** P0
**context:** domain
**BCPs:** 13
**status:** planned

## 1. Business narrative

El DownloadEngine es el corazón del proyecto: envuelve yt-dlp (con curl-cffi/impersonación TLS) para descargar vídeos de TikTok de forma resistente a bloqueos. Clasifica fallos (definitivo/transitorio/integrity, §4.3), aplica el cooldown global cross-proceso (§4.5), y gestiona el archivo de deduplicación (T24/T47/L-C8).

## 2. Actors

- **Monitor / Backfill / retry-failed** — usan el engine para descargar.
- **yt-dlp** — motor de extracción (nightly, curl-cffi).
- **Operador** — ve resultados vía CLI/bot.

## 3. Problem statement

TikTok bloquea clientes automatizados. El engine debe: usar yt-dlp con impersonación (L-D1), clasificar fallos correctamente (T5/T52/T53/T54/T55), respetar el cooldown global (T22/T62/L-C6/L-C7), y manejar timeouts sin corromper archivos (T23/T66).

## 4. Requirements

#### ADDED: DownloadEngine (Protocol, §4.2)
**After:** `core/download_engine.py` define `DownloadEngine` como `typing.Protocol` con `download`, `extract_profile`, `list_videos`, `validate_cookie`. yt-dlp SIEMPRE vía `asyncio.to_thread` con timeout 10 min/vídeo. Formato por defecto `bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/...` (§4.2), override `DOWNLOAD_FORMAT`. `merge_output_format=mp4`. `impersonate` = **objetos ImpersonateTarget** rotados (L-D1). Nunca httpx/requests a TikTok.

#### ADDED: Clasificación de fallos (§4.3, T5/T52/T53/T54)
**After:** Clasifica excepciones case-insensitive sobre la cadena completa (incl. causa encadenada): auth (`requiring login`, `log into an account`, `log in for access`, `captcha`, `banned`, `suspended`) → **definitivo** (T52); `status code 0` → **transitorio** (T53); `keeps sending the same page` → **transitorio** (T54); 403 sin hints de auth → **transitorio** (T5); contenido inexistente (404, `video unavailable`, status ≠ 0 desconocido) → **definitivo**. `error_category` persistido.

#### ADDED: Slideshow → skipped (T55)
**After:** Extracción sin formatos de vídeo (`expected_has_video=False`) → `status='skipped'`, se archiva el ID, **sin reintentos** ni categoría de fallo.

#### ADDED: Reintento de formato mejorado + descarte del archive (T24)
**After:** Si el archivo no tiene pista de vídeo habiendo sido esperada → descartar entrada del archive **ANTES** del reintento (T24) → reintentar con formato mejorado → verificar integridad.

#### ADDED: Cooldown global cross-proceso (§4.5, T22/T62/L-C6/L-C7)
**After:** `reserve()` atómico con `UPDATE download_pacing_state SET next_allowed_at=... RETURNING` (T22); sorteo uniforme [MIN, MAX] con RNG inyectable (T62); singleton con commit inmediato (L-C6); timestamps con milisegundos (L-C7). Se aplica antes de cada intento.

#### ADDED: Timeout sin corromper (T23/T66)
**After:** `asyncio.wait_for(to_thread, timeout)` (no mata el hilo nativo, T23 — documentar); reintento tras timeout escribe a `.retry-N` y renombra solo tras integridad (T66). I/O pesada (SHA-256, ffprobe, fsync) a `to_thread` (T12); ffprobe con `--` antes de la ruta (T13).

## 5. Solution and main flow

1. `core/download_engine.py`: Protocol + implementación.
2. `core/archive.py`: dedupe (L-C8: ambos formatos de línea, T47: última línea malformada tolerada, T24: descarte).
3. `core/pacing.py` (o en engine): cooldown cross-proceso (T22/T62).
4. `services/videos.py` (parcial): handle_download_result.

## 6. Alternative flows / edge cases

- **403 sin auth**: transitorio (T5).
- **status code 0**: transitorio (T53).
- **same page**: transitorio (T54).
- **Slideshow**: skipped (T55).
- **Timeout**: .retry-N + integridad (T23/T66).

## 7. Assumptions

- yt-dlp nightly + curl-cffi instalados (e01s01, verificado).
- `MAX_CONCURRENT_DOWNLOADS` default 1.

## 8. Constraints

- Nunca httpx/requests a TikTok (§4.2).
- impersonate = objetos (L-D1).
- Clasificación 3-vías (§4.3).
- Cooldown cross-proceso (T22).

## 9. Dependencies

- e01s01 (yt-dlp), e01s04 (modelos/DB), e02s02 (selfcheck targets).

## 10. Interfaces

- `core/download_engine.py` → Protocol.
- `core/archive.py` → add/discard/contains.
- Consumido por backfill (e04s02/03), monitor (e03).

## 11. Test plan

- `tests/downloader/test_engine.py`: formato, rotación targets, cooldown.
- `tests/downloader/test_failure_classification.py`: T5/T52/T53/T54/T55.
- `tests/downloader/test_pacing.py`: T22/T62.
- `tests/archive/test_archive.py`: L-C8/T47/T24.

## 12. Data

- `download_pacing_state`, `download_archive`, `videos`.

## 13. Security considerations

- Sin secretos; cookies rotadas internamente.

## 14. Performance

- to_thread para toda I/O pesada (T12).

## 15. Operational concerns

- Zombis de yt-dlp documentados (T23); expuestos en daemon status (T66).

## 16. Risks

- **Clasificación errónea**: T5/T52/T53/T54 (tests con literales reales).

## 17. Acceptance criteria

- [ ] `DownloadEngine` Protocol con download/extract_profile/list_videos/validate_cookie.
- [ ] Formato §4.2 + override; impersonate objetos (L-D1).
- [ ] Clasificación T5/T52/T53/T54/T55 correcta (tests con literales reales).
- [ ] Slideshow → skipped (T55).
- [ ] Reintento formato mejorado con descarte del archive (T24).
- [ ] Cooldown T22/T62/L-C6/L-C7.
- [ ] Timeout T23/T66.
- [ ] Archive L-C8/T47.
- [ ] Tests en `tests/downloader/` y `tests/archive/` pasan.

## 18. Out of scope

- Backfill (e04s02/03).
- handle_download_result completo (e04s02).
- Cookies (e05).

## 19. Risks (detailed)

- **Clasificación errónea** → bloqueos autoinfligidos: tests con literales reales del extractor.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/downloader/ tests/archive/` pasa.
- Tasks `status: passing` en `e04s01-tasks.yaml`.

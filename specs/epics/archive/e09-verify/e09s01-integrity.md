# Story e09s01 — videos integrity (tamaño + SHA-256 + ffprobe)

**type:** feat
**risk:** P1
**context:** domain
**BCPs:** 4
**status:** planned

## 1. Business narrative

Verificación de integridad de vídeos descargados: tamaño > 0 + SHA-256 + ffprobe (§4.6). Centralizada en `handle_download_result`; distingue slideshow (skipped, T55) de fallo real (integrity). Comando `videos integrity [username]` (§3).

## 2. Actors

- **Motor** — llama handle_download_result post-descarga.
- **Usuario** — `videos integrity [username]`.
- **ffprobe** — valida pista de vídeo/duración/codecs.

## 3. Problem statement

Sin verificación, archivos corruptos o solo-audio se marcan como descargados. La verificación debe: usar to_thread (T12), ffprobe con `--` (T13), y distinguir slideshow (no fallo, T55) de fallo real de integridad.

## 4. Requirements

#### ADDED: Verificación centralizada (§4.6)
**After:** `services/integrity.py` — `verify_video(path)` → tamaño > 0 + SHA-256 + ffprobe (pista de vídeo, duración > 0, codecs). `handle_download_result` la usa.

#### ADDED: I/O pesada a to_thread (T12) + ffprobe con -- (T13)
**After:** SHA-256 y ffprobe vía `asyncio.to_thread` (T12); ffprobe con lista de argumentos + `--` antes de la ruta (T13).

#### ADDED: Distinción slideshow vs fallo (T55)
**After:** `expected_has_video=false` → `status='skipped'` (sin reintentos, sin fallo); `expected_has_video=true` sin pista → `error_category='integrity'` (fallo real).

#### ADDED: videos integrity [username] (§3)
**After:** Verifica todos los vídeos de una cuenta o todos si no se especifica; reporta OK/FAIL por vídeo.

#### ADDED: Best-effort (T14)
**After:** Limpiezas posteriores a éxito confirmado son best-effort (no convierten éxito en fallo).

## 5. Solution and main flow

1. `services/integrity.py`: verify_video (tamaño + SHA-256 + ffprobe).
2. `services/videos.py`: handle_download_result con expected_has_video.
3. `cli/videos.py`: videos integrity [username].

## 6. Alternative flows / edge cases

- **Archivo ausente/0 bytes**: integrity (nunca downloaded).
- **Slideshow**: skipped (T55).
- **Nombre con -**: ffprobe con -- (T13).

## 7. Assumptions

- ffprobe disponible (selfcheck T46 lo verifica); modelos (e01s04).

## 8. Constraints

- I/O pesada a to_thread (T12).
- ffprobe con -- (T13).
- Nunca marcar downloaded sin verificar (§4.6).

## 9. Dependencies

- e01s04 (modelos), e02s02 (ffprobe selfcheck).

## 10. Interfaces

- `services/integrity.py` → verify_video.
- `services/videos.py` → handle_download_result.
- `cli/videos.py` → videos integrity.

## 11. Test plan

- `tests/integrity/test_integrity.py`: verify (tamaño/SHA/ffprobe mock), T13 (-- en ffprobe), T55 (slideshow vs integrity), T14.

## 12. Data

- `videos` (file_hash, status, error_category).

## 13. Security considerations

- Sin secretos.

## 14. Performance

- to_thread para hash/ffprobe (T12).

## 15. Operational concerns

- reporte OK/FAIL por vídeo.

## 16. Risks

- **Falso downloaded**: nunca marcar sin verificar.

## 17. Acceptance criteria

- [ ] verify_video: tamaño + SHA-256 + ffprobe (§4.6).
- [ ] to_thread (T12); ffprobe con -- (T13).
- [ ] slideshow → skipped (T55); fallo real → integrity.
- [ ] videos integrity [username] (§3).
- [ ] Best-effort (T14).
- [ ] Tests en `tests/integrity/` pasan.

## 18. Out of scope

- system backup (e09s02).

## 19. Risks (detailed)

- **T55**: distinción slideshow/integrity.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/integrity/` pasa.
- Tasks `status: passing` en `e09s01-tasks.yaml`.

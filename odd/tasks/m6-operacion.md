# m6-operacion — Operación y ronda en vivo (hito M6, §12.1)

Source of truth: `specs/Plan de implementacion tikdown-rs.md` §10.1-§10.3, §14.5-§14.6, §12.1
(criterio de aceptación M6: `system site render` genera los 4 archivos sin recorrer el sistema de
archivos; bug en vivo → test de regresión + fila en Apéndice A). Base: master `3548d86`.
Cada unidad = un commit revertible, tests primero. §0.4: mínimo diff, solo tablas/settings ya definidas.

## Estado previo (verificado)

- Settings `STATIC_SITE_*` y `SYSTEM_BACKUP_RETAIN_COUNT` ya existen en `core/config.py`.
- `total_disk_bytes` ya se mantiene en la misma transacción de cada descarga (T-DATA-10 OK, M2/M4).
- `core/disk.py::_backups_bytes` ya cuenta `<data_dir>/backups` (§8.2).
- ffprobe/ffmpeg detectados por selfcheck (`cli/daemon.py:286`).
- Stubs M6: `videos last/export/integrity`, `system backup`, `system site render` (`raise_unimplemented`).

## Tasks

- [x] T1 — `services/status.py`: commit `77c8b8c` (19 tests nuevos; suite 685 passed ×2, ruff
      clean). CLI y bot delegan (duplicación del dispatcher eliminada — mejora M8). Salida de
      `daemon status` sin cambios de wording (regla de fidelidad); disco recolectado pero no impreso
      aún — nota: §10.1 incluye disco y el hallazgo M20 lo señala; imprimir la línea de disco va en
      T8. Shim compat `_format_status_lines` en cli/daemon.py pendiente de retirar cuando
      tests/test_monitor_state.py entre en superficie de edición.
- [x] T2 — `videos last [N]` + `/last` del bot: commit `15d31e7` (T2+T3 juntos). Ordenamiento
      `downloaded_at DESC NULLS LAST, id DESC` (el modelo no tiene `uploaded_at`; `upload_date` es
      texto YYYYMMDD no ordenable); N default 10, techo 100 (clamp en el servicio, compartido
      CLI/bot); `last` excluye pending; reply HTML escapado + clip; empty reply informativo.
- [x] T3 — `videos export [--format json|csv]`: commit `15d31e7`. Stdlib csv (T-CLI-3),
      sanitización T-DEPLOY-16: prefijo `'` cuando el PRIMER char es `= + - @ \t \r` (aplicado tras
      `str()` a toda celda); export incluye TODOS los estados (dump completo del archive),
      `last` excluye pending. Payload crudo por stdout.
- [x] T4 — `videos integrity [username]`: commit `f005553` (12 tests nuevos; suite 723 passed,
      ruff clean). Campo real `local_path` (no `file_path`); SHA-256 streaming 1 MiB via to_thread
      (reuso `_sha256_file`); ffprobe = `shutil.which` (patrón selfcheck) + `subprocess.run` en
      to_thread con `--` antes del path (T-ENGINE-24); sin ffprobe → WARNING + `ffprobe_skipped`
      (no es fallo); read-only puro (test fija que no hay commits); exit 0 con filas marcadas
      (diagnóstico, no error). Categorías disjuntas que suman total_checked.
- [x] T5 — `system backup`: commit `3084c72` (6 tests nuevos, 1 skip por chmod en Windows;
      suite 728 passed ×2, ruff clean). `VACUUM INTO` con sesión AUTOCOMMIT (VACUUM no corre en
      autobegin de SQLAlchemy); snapshot `tikdown-rs-YYYYMMDDTHHMMSSZ.db` en `<data_dir>/backups/`;
      colisión mismo-segundo → overwrite determinista; chmod 0600 best-effort (PermissionError
      swallow en Windows); retención con pattern-scoping (borra solo snapshots), campo verificado
      `system_backup_retain_count` (config.py:87); devuelve `(Path, int removed)`.
- [x] T6 — `services/static_site.py::render_site`: commit `608b3e1` (18 tests nuevos; suite
      746 passed ×2, ruff clean). 4 archivos atómicos (temp + os.replace); JSON embebidos en
      `<script type="application/json">` (funciona en file:// sin servidor), `</` escapado;
      Columna 1 reusa `gather_status` + `last_known_good_ytdlp_version` ("versión más nueva"
      omitida: §8 no la trackea — regla de admisión); Columna 2 solo columnas de
      `monitored_accounts` (T-DATA-10, verificado con decoy junk en disco); Columna 3 reusa
      `_video_rows` limitado + fallos agrupados por `error_category`; Columna 4 con
      `command_reference` inyectado como parámetro (services no importa cli — arquitectura verde):
      CLI pasa árbol typer introspectado, job del daemon pasa None (placeholder); UI en español
      (consistente con el bot); job del scheduler solo si `STATIC_SITE_ENABLED` (default apagado,
      contratos de 7 jobs intactos); sin secretos (test siembra cookie blob + token y afirma
      ausencia en los 4 archivos).
- [x] T7 — `accounts stats` + `/stats` del bot: commit `f51a403` (13 tests nuevos; suite
      758 passed ×2, ruff clean). `account_stats` read-only con counts por status y
      `total_disk_bytes` de la columna (T-DATA-10); CLI sin args (§10.1) con label "(approx)";
      bot `MSG_STATS_PENDING` eliminado, reply HTML escapado + clip; duplicación mínima de query
      con static_site (unificación futura fuera de superficie).
- [x] T8 — Gate M6: suite `758 passed, 1 skipped, 1 deselected` (verde serial ×6 consecutivas);
      ruff check + format limpios; árbol CLI 1:1 con §10.1 — **cero stubs restantes**
      (última: `accounts stats`, `f51a403`). README: secciones Operation + Troubleshooting
      (§14.5/§14.6) en `d05e532`. Mejora M20 cerrada: `daemon status` imprime
      `disk_free_percent` (línea en `format_status_lines`, d05e532).
      **Flake de timing documentado**: `test_daemon_lifecycle` falla ~1/3 corridas SOLO bajo
      contención (dos suites pytest en paralelo en la misma máquina — causa identificada en esta
      sesión); en serial es estable. No es defecto del producto.
- [ ] Pendiente usuario — Ronda en vivo contra TikTok real (datos desechables) + verificación
      empírica de recuperación de polling (M5 §6.5).

## Ronda en vivo (2026-09-27/28, @rosary657, DATA_DIR desechable en tikdown-rs-trash/live-data)

Resultado: **ronda completa exitosa**. Cookies: import → `inconclusive` → sonda real → `valid`
(WAF challenge PoW resuelto por el extractor). Backfill history completo: **17/17 videos
`downloaded`, 30.3 MB en disco, `videos integrity` 17/17 ok**. Un 403 transitorio absorvido por el
embudo. Export json/csv OK. Backup OK (VACUUM INTO + retención). `system site render` OK: los 4
archivos con datos reales. Daemon: heartbeat 10 s, healthcheck exit 0, jobs al día, `daemon stop`
limpio (T-CLI-6), estado saneado.

**4 bugs reales cazados en vivo — cada uno con test de regresión + fila en el Apéndice A:**

1. `T-ENGINE-31` (`f23ff10`): `accounts add` persiste la URL verbatim → URL basura en el motor →
   listado vacío silencioso. Fix: `normalize_handle` en el motor (cubre backfill/monitor/perfil).
2. `T-ENGINE-32` (`53b204f`): yt-dlp salta archivos existentes con `requested_downloads` vacío →
   `retry-failed` nunca convergía. Fix: adopción por glob-by-id (nunca `.part`).
3. `T-ENGINE-33` (`98a1d08`): argv de ffprobe con `--` mal ubicado + secciones con `,` en vez de
   `:` → `has_video` SIEMPRE False → TODO download degradaba a `failed/integrity`. Invisible a los
   tests (ffprobe stubbeado — punto ciego del mock). Fix: `_ffprobe_command` puro + test de
   integración real skipif.
4. `T-CLI-11` (`04fd852`): export en consola Windows cp1252 revienta con títulos unicode. Fix:
   UTF-8 explícito por `sys.stdout.buffer`.

Entorno: ffmpeg/ffprobe portable en `tikdown-rs-trash/ffmpeg/` (build gyan 7.1.1) agregado al PATH
solo para la ronda (equivalente al binario que la imagen Docker lleva baked). Suite al cierre:
**774 passed, 2 skipped (Windows chmod + ffprobe-integration), 1 deselected**.

Pendiente de la ronda: verificación empírica de recuperación de polling (M5 §6.5) — requiere
TELEGRAM_BOT_TOKEN (el usuario eligió ronda solo-CLI). Ciclo de monitor con video NUEVO real no
observado (la cuenta no publicó durante la ronda; el listado real ya quedó validado por el
backfill).

## Verificación empírica de recuperación de polling (M5 §6.5) — COMPLETADA 2026-09-28

Token real (@tik_cli_bot), chat real del usuario (42867075), PTB 22.8 real, `getUpdates` manual
usado SOLO como herramienta de prueba documentada (T-BOT-2: nunca en producción).

1. **Arranque real del bot en el daemon**: `getMe` 200 → `initialize/start/start_polling(25)` →
   `polling started (supervised)`. Los 2 `/start` pendientes fueron RE-ENTREGADOS por PTB al
   arrancar (T-BOT-4) y respondidos dos veces (idempotencia verificada en vivo).
2. **Conflicto 409** (sesión manual de `getUpdates` en paralelo, 40 s): el polling del bot recibió
   `Conflict: terminated by other getUpdates request`; el supervisor (healthcheck `getMe`) siguió
   200 — **el diseño §6.5 no detecta un 409 con API viva**, y PTB **se auto-recuperó** al cesar el
   conflicto (409 en 01:24:18 → 200 en 01:24:44, sin intervención). Conclusión: el caso 409 se
   auto-resuelve por reintento de PTB; el supervisor cubre la caída observable por `getMe`.
3. **Recuperación del supervisor verificada** (fallo inyectado SOLO en el healthcheck, lo que el
   supervisor observa; todo lo demás real): 2 fallos consecutivos de `get_me` → `bot unhealthy...
   restarting bot` → `stop()` + `start()` reales (T-BOT-1) → `restarting=False`, contador en 0
   (reset por éxito), `getMe` real 200, `sendMessage` real entregado al chat del usuario.

**Criterio de aceptación M5: CUMPLIDO.** Con esto el plan M0-M6 está completo.

## Review nativo encadenado M5+M6 (2026-09-28, en curso)

El candidato completo (6819 líneas, 22 commits desde `1c3d342`) excede el presupuesto de contexto
(`lens_context_budget_exceeded`, sin autoridad creada — stop terminal del provider). Plan: revisión
encadenada por cortes en el worktree temporal `<repo-root>/../tikdown-rs-review` (worktree temporal)
(rama `review-chained`), avanzando HEAD por corte, cada corte = 1 lineage nuevo con 4 lentes.

| Corte | Rango (baseRef..HEAD del worktree) | Líneas | Estado |
|---|---|---|---|
| 1 | `1c3d342..2804775` (prereq + security + paginación) | 697 | **APPROVED 4/4** — lineage `review-b807aefa95bdb6b8`, acknowledge quemado. 11 hallazgos informativos (2 WARNING: allowlist vacía fail-open en security.py:95-96; clamp de paginación pagination.py:103-104). Un relay fallido ("Stream ended without finish_reason") resuelto a slot en reintento. |
| 2 | `2804775..0459ef6` (dispatcher core T5a) | 1071 | **APPROVED 4/4** — lineage `review-f183bbcd49f2483e`, acknowledge quemado. 4 hallazgos informativos no bloqueantes (1 WARNING: hueco de ejecución de query tests/test_bot_dispatcher.py:502-525; resto SUGGESTION). |
| 3 | `0459ef6..7c74ea4` (mutadores + upload T5b) | 725 | **APPROVED 4/4 CON CORRECCIÓN** — lineage `review-539f12154b07b63f`, acknowledge quemado. Historia: 2 intentos murieron contra el bound derivado (stream-end a 781 s, timeout a 944 s); slot withdraw y re-ofrecido; a la tercera el slot `review-reliability` cerró con **R3-1 (CRITICAL, determinista)**: `format_pause_message` interpolaba `display_username(username)` sin `escape_html` en un reply con `parse_mode=HTML` (viola T-BOT-9). Corrección acotada de 7 diff lines (plan forecast 7, ejecutado 6+1) en commit `81b52bd` sobre la rama `review-fix-r3-1`; 654 tests passed (1 deselected live); validador dirigido PASSED. R3-1 era el único interpolado sin escapar del corte (add/remove/backfill sí escapaban). |
| 4 | `7c74ea4..d648475` (supervisión + daemon T6/T7) | 682 | **APPROVED 4/4 CON CORRECCIÓN** — lineage `review-0907af71ea9d74a3`, acknowledge quemado. 3 CRITICAL confirmados por refuter: R3-001/R3-003 (etapas del lifecycle PTB sin deadline, wedges startup/shutdown) y R3-002 (determinista: `_start_bot` no atómico, bot parcial sin shutdown). Corrección de 157 diff lines en commit `b35e14f` (rama `review-fix-r4`): `LIFECYCLE_STAGE_TIMEOUT_SECONDS=30` vía `asyncio.wait_for` en todas las etapas + cleanup `stop_bot_polling` antes de re-lanzar. Validador PASSED (4 advisory). |
| 5 | `d648475..77c8b8c` (docs M5 + services/status) | ~1138 | **APPROVED 4/4** — lineage `review-07c55dcf25c8d106`, acknowledge quemado. 2 WARNING informativos (R3-ROW-COMPAT, R3-TZ-NAIVE-NOW en services/status.py). Un relay timeout (951 s) resuelto con withdraw + reintento. |
| 6 | `77c8b8c..15d31e7` (videos last/export + /last bot) | 574 | **APPROVED 4/4** — lineage `review-f8a4c42e949b687e`, acknowledge quemado. 5 hallazgos informativos (4 readability + 1 resilience WARNING/SUGGESTION). |
| 7 | `15d31e7..3084c72` (integrity + backup) | 734 | **APPROVED 4/4 CON CORRECCIÓN** — lineage `review-db748f8801a726d0`, acknowledge quemado. 3 CRITICAL: R3-001/R4-001 (deterministas: `create_backup` hacía `unlink` del snapshot previo antes del VACUUM → colisión mismo-segundo + fallo destruía la última copia buena) y R4-002 (retención contaba `.db` parciales). Corrección de 89 diff lines en commit `cb18f3f` (rama `review-fix-r7`): publicación atómica (stage `*.db.tmp` + `os.replace`). Validador PASSED (16 advisory). |
| 8 | `3084c72..f51a403` (static site + stats) | 1229 | **APPROVED 4/4** — lineage `review-6c5f27e4831dea73`, acknowledge quemado. 5 WARNING informativos (static_site.py, dispatcher.py). Entró completo sin dividir; 1 relay timeout resuelto con withdraw + reintento. |
| 9 | `f51a403..e4475f6` (README + M20 + fixes en vivo + docs + re-review) | ~613 | **APPROVED 4/4** — lineage `review-88516330b1f72919` (re-review con candidato nuevo), acknowledge quemado. 9 WARNING informativos (ytdlp_engine.py adopt-glob, guards de tests ffprobe/ffmpeg, orden de strip de handle). El fix T-CLI-11 (`6765edd`) resolvió la pared del admission: readability entró al 2do intento (1 relay timeout con "Stream ended without finish_reason" resuelto con STATUS fresco + binding re-ofrecido). Fixes de cortes 4 y 7 mergeados a master (`ee098d9`, `5c168ae`); worktree de review removido. |

Receta por corte (probada en los cortes 1-3): `git -C <worktree> checkout <head-del-corte>` →
`gentle_review inspect` (workspaceRoot=worktree) con `{"baseRef": "<base-del-corte>",
"committedOnly": true}` → START ordinary + idempotencia fresca → STATUS → capturas slot a slot
(forecast + ack) → acknowledge-approved verbatim. NUNCA reusar bindings.

Si un corte vuelve `correction_required`: STATUS bound (con `input` baseRef+committedOnly, o el
provider resuelve el workspace ambiente y devuelve `unrelated`) → `gentle_review_capture` con
`correctionLines` = forecast en diff lines **antes de editar** → editar → commit (el candidato
corregido TIENE que ser un commit: un lineage committedOnly no ve el working tree) → STATUS →
slot `provider_targeted_validator` (forecast + ack) → acknowledge-approved. Un solo presupuesto
de corrección: si el validador falla, escala.

Dos lecciones duras:

1. **El bound del relay solo lo sube el env var.** `lib/review-host-relay.ts:458-472`: bound =
   900 s + 900 s por MiB de prompt, techo 7.200 s, y `GENTLE_PI_REVIEW_RELAY_PI_TIMEOUT_MS` lo
   reemplaza entero leyéndolo del proceso pi. Dividir el corte NO ayuda: el piso ya es 900 s.
2. **Transcribir un `collectBinding` a mano**: el parámetro NO se desescapa (lo que se escribe
   llega literal) y el output del provider es fiel, así que los `\n` de `proof`/`policyContent`
   se escriben con **un** backslash, no dos. Un binding rechazado no muta nada
   (`capture-binding-rejected`), y el binding exacto se puede extraer del transcript de sesión
   (`~/.pi/agent/sessions/<proyecto>/<ts>_<id>.jsonl`, record `toolResult`) para comparar.

Tres lecciones más (cortes 4-8):

3. **El plan de corrección va ANTES del commit.** Si el candidato committed cambia de identidad
   antes de capturar `correctionLines`, el binding (target original) es rechazado por la facade
   ("does not carry one non-empty matching provider lineage and target token"). Ante ese rechazo
   ya-commiteado: `git reset --soft HEAD~1`, capturar el plan, re-commitear (aplicado en corte 7:
   `5dc9bea` descartado, `cb18f3f` final).
4. **Facade STATUS atascada con `capture-route-registration-rejected` persistente**: las rutas de
   captura retenidas llevan `baseRef=undefined` si la primera STATUS del lineage fue sin `input`;
   una STATUS posterior CON baseRef colisiona (`gentle-ai.ts:6543`). Solución: llamar STATUS SIN
   `input` (sin baseRef) — el provider igual resuelve el target correcto.
5. **El admission NO soporta paths con espacios** en `location`/`proof_refs`: parte el `path:línea`
   en el primer espacio. Los rechazos NO consumen el slot ni el presupuesto (reintento barato,
   ~2 s), pero un hallazgo que DEPENDE de citar ese path entra en loop (caso del corte 9).

## Notas

- `/monitor` y `/check` del bot siguen pendientes de la ronda de listado real (T-ENGINE-19);
  fuera de alcance M6 salvo que la ronda en vivo los habilite.
- Los 4 archivos del panel se escriben en cada regeneración; JS cliente filtra/ordena (§10.3
  regla de admisión).

## Cierre de proyecto (2026-09-28, sesión final)

Estado de entrega: **M0–M6 completos, review RDD 12/12 candidates APPROVED, backlog de mejoras cerrado.**

- **Review encadenado M5+M6**: 9/9 cortes APPROVED con acknowledge quemado (corte 9 re-review
  con candidato `f51a403..e4475f6`, lineage `review-88516330b1f72919`; el fix T-CLI-11 resolvió
  la pared del admission con paths con espacios).
- **Fixes de review mergeados a master**: `ee098d9` (corte 4) y `5c168ae` (corte 7). Ramas de
  review eliminadas; worktree de review removido.
- **Auditoría del backlog M1–M23** (subagente read-only): 2 ya resueltas de facto (M12, M18),
  3 confirmadas cerradas (M5/M8/M20), 1 obsoleta (M14), y **17 corregidas en dos lotes**:
  - Lote 1 (base `275615a`, lineage `review-538d7c685ea5723c`): M16, M6, M22, M7, M15, M11, M13
    + T-DEPLOY-24 (engine perezoso, hallazgo Docker) + compose nginx.
  - Lote 2 (base `2a9a624`, lineage `review-91e0af0ef288948b`): M1 (proxy/extractor-args),
    M3 (gate de red en backfill), M9 (contadores en daemon_state + migración 0003),
    M10+M14 (código muerto retirado), M21 (flags adoptados a §10.1), M23 (higiene de tests).
  - Quedan abiertas SOLO las 3 decisiones de diseño del dueño: M2 (rotación de cookies),
    M4 (healthcheck vs runtime cookies), M19 (stop-window residual §5.2).
- **Prueba Docker §14.2 completa contra TikTok real**: build, selfcheck, cookie valid, backfill
  17/17 con integridad 17/17, transición --then-monitor, T-DEPLOY-13 (stop limpio sin restart),
  site render 4 archivos. Hallazgo documentado como **T-DEPLOY-24** (Apéndice A).
- **Stack de pruebas**: docker compose (daemon + nginx sitio en :8080) con datos desechables en
  `tikdown-rs-trash/docker-data`; verificación de feed en vivo (17/17, cero videos nuevos).
  Contenedores detenidos al cierre por decisión del operador; imagen y datos se conservan.
- **Entorno local**: `tikdown-rs-trash/tikdown.cmd` (DATA_DIR=live-data, token desde txt,
  ffmpeg en PATH); daemon del host lanzable con PowerShell `Start-Process`.
- Suite al cierre: **790 passed, 2 skipped, 1 deselected**; ruff check + format limpios.
  Flake documentado: `test_daemon_lifecycle` bajo contención (estable en serial).
- Métricas finales: ~22.9k líneas de Python (9.5k src / 13.4k tests), master `627c5fb`.

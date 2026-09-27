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

## Notas

- `/monitor` y `/check` del bot siguen pendientes de la ronda de listado real (T-ENGINE-19);
  fuera de alcance M6 salvo que la ronda en vivo los habilite.
- Los 4 archivos del panel se escriben en cada regeneración; JS cliente filtra/ordena (§10.3
  regla de admisión).

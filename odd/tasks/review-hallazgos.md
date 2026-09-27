# Reporte consolidado de revisión M0–M4 — clean context (capa 1 + Judgment Day)

Base revisada: master `d2bcb78` · Método: Capa 1 (3 scouts guiados con plan + hotspots del
handoff, verificación del gate) y Capa 2 (Judgment Day: jueces A=10 filas, B=13 filas,
lanzados en paralelo, ciegos entre sí y de la Capa 1). Capas comparadas solo al final.
Veredicto JD original: ESCALATED (0 rondas de corrección — los fixes se aprobaron después).

---

## BLOQUEANTES (8) — TODOS CORREGIDOS en `fix/review-bloqueantes`

| # | Hallazgo | Ubicación original | Justificación | Capa(s) | Commit |
|---|---|---|---|---|---|
| B1 | Listados yt-dlp (`list_videos`, `extract_profile`, sonda de cookies) bloqueando el event loop → heartbeat > 3× ventana → Docker HEALTHCHECK reinicia en pleno backfill | `backfill.py:404`, `monitor.py:160`, `maintenance.py:142`, `run.py:346` | §1.1.3, T-ASYNC-8, T-DEPLOY-7 | A+B+C1 | `539d7e0` |
| B2 | `'flat_playlist': True` es clave inerte (solo `extract_flat` se lee); T-ENGINE-12 no neutralizado | `verify.py:41`, `ytdlp_engine.py:130` | §4.6, T-ENGINE-12, §2.3 | C1 (verificado en vivo) | `279abc6` |
| B3 | `MONITOR_INTERVAL_MINUTES` sin consumidor: ciclo del monitor cada ~30 s (10× lo configurado) | `config.py:57`, `run.py:218` | §11.1, T-DEPLOY-9, T-DATA-9 | A+B | `4a912bf` |
| B4 | `NETWORK_PROBE_URL` default `""` → offline permanente con red sana | `config.py:77`, `network_monitor.py:40-52` | §8.1, §11.1, §0.2.3 | A+C1 | `a21bc23` |
| B5 | Excepción no-`CancelledError` deja cuenta wedged en `backfilling` para siempre | `backfill.py:534` | §9.1, T-BACKFILL-6 | A | `2caef2a`+`95e4fb0` |
| B6 | Cursor `<=` en vez de `<` estricto: salta same-day + `retry-failed` inalcanzable | `backfill.py:441` | §9.2, T-BACKFILL-19 | A+B | `c893cd9` |
| B7 | Backfill sin `expected_has_video` → slideshows como `failed/integrity` en loop; `retry_fn` código muerto; retirado con DR-18 | `backfill.py:462-512`, `videos.py:268-277` | §4.7, T-ENGINE-5/18 | A+B+C1 | `d0b0bad` |
| B8 | Reanudación automática de `paused` camino muerto (`not_queued` eterno) + churn de breaker | `backfill.py:296-330,392` | §9.1, T-BACKFILL-6 | C1+B | `cbfdd59` |

## MEJORAS (M1–M23) — ABIERTAS

| # | Hallazgo | Justificación | Capa(s) |
|---|---|---|---|
| M5 ⚠️ | `python-telegram-bot` sin extra `[rate-limiter]`: `AIORateLimiter` revienta al construir el bot — **prerrequisito duro de M5** | §2, T-BOT-8 | C1 |
| M1 | `YTDLP_PROXY_URL` / `YTDLP_EXTRACTOR_ARGS` sin consumidor: el operador cree ocultar su IP y va directo. Cablear o retirar | §11.1, T-DEPLOY-9, T-ENGINE-29 | B |
| M2 | Motor con blob de cookies fijo: sin rotación round-robin (§4.3); cookie muerta → cuentas sanas en `needs_review` | §4.3, §6.1 | B |
| M3 | Backfill sin gate de red en flujo normal: una caída quema el feed y avanza el cursor | §8.1, T-BACKFILL-17 | B |
| M4 | `healthcheck` exige `valid` pero el runtime acepta `inconclusive` (T-COOKIES-4): instalación sin `COOKIE_VALIDATION_URL` = contenedor unhealthy para siempre | §10.1, T-COOKIES-4 | B+C1 |
| M6 | `requires-python = ">=3.13"` sin techo `<3.14` | §2 [N], B.3 | C1+A |
| M7 | `run_or_exit` solo atrapa `ConfigurationError`: errores de negocio/DB salen como traceback | §10.2, T-CLI-8/4 | C1 |
| M8 | Lógica de status/healthcheck en la capa CLI; no hay `services/status.py` del árbol §12 | §10.2, §16.1 | C1 |
| M9 | Contador de hilos zombis incrementado pero nunca leído; `supervised_tasks`/zombies "n/a (in-process)" | §0.3, §4.5, T-ASYNC-14 | C1 |
| M10 | `sync_from_file` del archive sin llamador de producción | §0.3, §3.6 | C1 |
| M11 | `upload_date=None` persistido; el cursor no avanza con entradas sin fecha | §4.6, T-BACKFILL-3 | B |
| M12 | `needs_review=True` al primer fallo *definitivo* (incluye 404/removed, no solo auth) | §9.6, §4.4 | A |
| M13 | `following_count`/`total_likes` sobrescritos a NULL en cada refresh (ProfileData no los trae) | §3.1 | A |
| M14 | `NetworkMonitor.run_forever` (backoff 30→120 s) sin llamador: el probe corre fijo | §0.3, §8.1 | B |
| M15 | `env.py` de Alembic sin PRAGMAs (migra sin `busy_timeout` ni WAL) | §3.7, T-DB-5 | C1 |
| M16 | Cooldown `0/0` inalcanzable por entorno (`gt=0`) | §4.5, T-ENGINE-26 | C1+A |
| M17 | Timeout por vídeo puede llegar a 2× con el embudo DEFAULT+FALLBACK | §11.2 | C1 |
| M18 | `--then-monitor` en dos commits, no en la misma transacción (mitigado por reconcile) | §9.5, T-BACKFILL-16 | C1 |
| M19 | Apagado §5.2 pasos 5-6 parciales: jobs del scheduler fuera del registro supervisado | §5.2, T-ASYNC-9/11 | C1 |
| M20 | `daemon status` no imprime la línea de disco | §10.1 | C1 |
| M21 | Smoke test valida grupos pero no flags extra (`--label`, `notify [user]` exceden §10.1) | §10.1, T-CLI-5 | C1 |
| M22 | `[project] authors` sin la identidad DR-14 | §15.3, DR-14 | C1 |
| M23 | Tests: `mkdtemp()` real fuera de `tmp_path`; sleeps reales en test de proceso | §13.1, T-DEPLOY-18/19 | C1 |

## COSMÉTICOS

- Sonda de impersonación colapsa causas 2/3 en un solo motivo (deuda del snippet §4.1) — C1
- `ensure_daemon_state_row` sin llamador de producción (los upserts la crean de facto) — C1
- `.gitignore`: `*.db*`/`videos/` sin anclar (hoy inofensivo; `/cookies*` anclado) — C1
- pytest no imprime línea de resumen final (gate confirma por exit code) — verify
- Open item (a) handoff: wedge residual stop-window — documentado, registrado para M6 — C1

## Convergencia entre capas

- 3 capas (máxima confianza): B1, B7, B4.
- Jueces ciegos convergentes: B3, B6, M4.
- Solo Capa 1: B2, M5-M10.
- Solo jueces: B5, B6, M1, M2, M3, M12, M13, M14.
- Open items del handoff: (a) mitigación parcial→M6, (b)→M9, (c)→M4, (d) raza benigna, (e) sin violación §15.2.

## Estado de corrección

- Bloqueantes: **8/8 corregidos** en `fix/review-bloqueantes` (commits en
  `odd/tasks/review-bloqueantes.md`), gate 530 passed. Ver ledger para detalles de cada fix.
- Mejoras y cosméticos: abiertos, pendientes de aprobación del usuario.
- Review nativo RDD: PAUSADO (ver sección del ledger); candidata sin review.

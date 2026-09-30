# Guía del proyecto — cómo avanzó tikdown-rs

Guía de cómo este proyecto se construyó: las decisiones de proyecto y los
inconvenientes reales que aparecieron en el camino. Todo lo que aquí se describe
son fracasos del **proyecto** contra el entorno real (TikTok, SQLite, Docker,
Windows) — no incidencias de herramientas. Fuentes: `specs/Plan de implementacion
tikdown-rs.md` (especialmente Apéndice A — Trampas verificadas y Apéndice C —
Decisiones de diseño), el git log (135 commits) y los hitos en `odd/tasks/`.

## 0. El método: el plan es la fuente de verdad

El proyecto arrancó al revés de lo habitual: primero un **plan normativo** de
~1.900 líneas (`specs/Plan de implementacion tikdown-rs.md`) con:

- secciones sustantivas (§1–§11): qué es el sistema, no cómo escribirlo;
- Apéndice A: una tabla de **trampas verificadas**; cada fila con su evidencia —
  `[real]` (se rompió de verdad contra el sistema real y no se discute),
  `[doc]` (restricción derivada de documentación oficial de una dependencia) o
  `[raz]` (razonamiento sin incidente: no es ley, pero tampoco se borra sin un
  test barato que la cubra);
- Apéndice C: decisiones de diseño numeradas `DR-N`;
- un solo esquema de identificadores `T-<ÁREA>-N`, citables desde el código y
  desde secciones normativas.

Regla operativa central: cada bug real corregido debía **agregar una fila** al
Apéndice A (y a las tablas de síntomas de §14.6) en el mismo turno. Eso convirtió
cada inconveniente en documentación permanente; casi todos los ejemplos de esta
guía salen de ahí.

El trabajo avanzó por hitos M0–M6, cada uno con criterio de aceptación
verificable, y revisión por cortes antes de cerrar cada bloque (12/12 cortes
aprobados en la revisión final).

## 1. M0 — Base: el andamio que evita clases enteras de dolor

- **Config fail-fast** (`Settings` con pydantic-settings): una variable mal
  tipeada avisa en vez de ensuciarse silenciosamente (T-DATA-5). Además, el
  `.env.example` está sincronizado contra `Settings` por un test (T-DATA-4): la
  primera revisión encontró 7 variables muertas y 19 sin documentar — un test
  evita que eso vuelva a pasar.
- **Migraciones idempotentes con tabla marcadora**: `stamp("head")` sobre una
  base ya marcada pero anterior a una migración posterior la deja marcada como
  actualizada sin haber recibido la migración (T-DB-10); el síntoma explota mucho
  después. El stamp apunta a la revisión exacta que creó el marcador, nunca a
  `head` (DR-12).
- **JSON + archivo rotante** de logging desde el día uno (proceso daemonizado).

## 2. M1 — Datos y cookies: la superficie más delicada

Las cookies son el recurso más frágil del sistema: una cookie inválida sin
reemplazo deja la cuenta sin monitor.

- **Parser Netscape estricto**: se probó un parser propio tolerante y se volvió
  al header Netscape obligatorio — los archivos mal formados deben fallar
  temprano, no enmascararse (T-COOKIES-1). Los tests corren con el
  `YoutubeDLCookieJar` real, no con parseos simulados.
- **Tempfiles en Windows**: un `mkstemp` con fd abierto no se puede borrar en
  Windows (T-COOKIES-7); regla de proyecto: `os.close(fd)` inmediato en todo
  tempfile.
- **Sonda de validación**: una cookie válida puede dar `inconclusive` (sonda
  inalcanzable, embedding deshabilitado, primera entrada slideshow) → iterar 5
  entradas (T-COOKIES-2); una sonda rota nunca invalida cookies (T-COOKIES-3) y
  `get_working_cookie` solo rechaza ante `invalid`, nunca ante `inconclusive`
  (T-COOKIES-4).
- **Sin cifrado en reposo** (DR-3): se evaluó cifrar las cookies en la base y se
  decidió no hacerlo — el modelo de amenazas no lo justifica, y una clave perdida
  ya implica pérdida irrecuperable. Menos código, menos superficie de fallo; la
  protección real son permisos del volumen restringidos y backups `chmod 0600`
  con publicación atómica.

## 3. M2 — Motor de descarga: el terreno de TikTok

Donde se concentraron las trampas `[real]` más costosas:

- **Clasificar el error antes de actuar**: un único clasificador decide si un
  fallo es transitorio (403/429, timeout, challenge WAF, degradación del
  extractor) o definitivo. Un 403 genérico tratado como definitivo **pausaba
  cuentas sanas** (T-ENGINE-1). La regla resultante rige todo el sistema: **un
  fallo transitorio nunca es un defecto** — no pausa cuentas, no invalida
  cookies, no consume reintentos.
- **Formato**: la rama DASH resulta bloqueada por TikTok; el orden por defecto es
  progresivo primero, medido y sobre-inscribible vía `DOWNLOAD_FORMAT`, y se
  re-mide antes de cambiar el default (T-ENGINE-10, DR-4).
- **Listado**: sin `impersonate` a nivel de parámetros (reducía la extracción a
  1 entrada — T-ENGINE-11), con `flat_playlist=True` siempre (listar resolviendo
  cada entrada se bloquea intermitentemente — T-ENGINE-12) y filtrando entradas
  `None` que `ignoreerrors` deja pasar (T-ENGINE-13).
- **curl-cffi pineado**: una serie incompatible de `curl-cffi` producía todos los
  targets `unavailable` y 403 silenciosos (T-ENGINE-22). Se pinea vía el extra
  `pin-curl-cffi` de la propia nightly — nunca a mano. El pin yt-dlp se saca del
  canal nightly; la normalización PEP 440 de PyPI no coincide con el tag de
  GitHub: comparar siempre contra `yt_dlp.version.__version__` (T-ENGINE-20/21).

## 4. M3 — Backfill: estado en SQLite como única infraestructura

Decisión de base: **cada capacidad necesita su llamador real** — un componente
completo y testeado pero sin nadie que lo llame en producción es deuda (T-DATA-2).
Inconvenientes reales del hito:

- El progreso del backfill se calculaba sobre una variable todavía `None` y
  quedaba `N/0` (T-BACKFILL-4/5); la persistencia de progreso pisaba un cancel
  concurrente (T-BACKFILL-7); la cancelación se pisaba con `completed` y
  disparaba la transición dos veces (T-BACKFILL-8).
- Correcciones: UPDATE condicional con `WHERE backfill_status='backfilling'`,
  misma transacción para history→monitor y completado (T-BACKFILL-16), y
  coordinación cross-proceso con slot y reloj atómicos en SQLite
  (`INSERT ... ON CONFLICT DO NOTHING` + relectura — T-BACKFILL-20/B-6/11/12).
- El archive de yt-dlp escribe `tiktok <id>` y el código esperaba `<id>`: el ID
  es siempre el **último token** de la línea (T-DB-8).

## 5. M4 — Daemon: un solo loop y tareas supervisadas

El daemon junta asyncio + scheduler + bot + yt-dlp en threads nativos:

- **Un único `asyncio.run(_lifecycle())`**: un `asyncio.run()` por fase dejaba el
  scheduler en un loop muerto y el watcher de parada sin disparar (T-ASYNC-3).
  Reglas del hito: callbacks `add_done_callback` siempre síncronos, toda tarea de
  fondo por el registro de tareas supervisadas, I/O pesada (ffprobe, SHA-256,
  fsync, migraciones) en `to_thread`, jobs con `max_instances=1` +
  `coalesce=True` (un job más lento que su intervalo se solapea — T-ASYNC-13), y
  releer banderas de `daemon_state` en cada ejecución (un job que cachea la
  bandera al registrarse nunca ve el cambio — T-ASYNC-16).
- **Apagado real**: drenaje por registro de tareas supervisadas
  (`AsyncIOScheduler.shutdown(wait=True)` no espera — T-ASYNC-9), evento de
  notificación emitido antes del drenaje (T-ASYNC-6), y salida 0 en parada limpia
  porque la política de reinicio del contenedor depende de ella (T-DEPLOY-13).
- **Clase de fallo estructural**: la red bajo el sistema nunca produce fallos de
  cuenta ni de cookie (T-BACKFILL-17) — una caída de red no escribe
  `validation_state='invalid'`, no consume reintentos, no cuenta para breaker.

## 6. M5 — Bot de Telegram

- **Polling defensivo**: nunca un `getUpdates` manual (crea 409 Conflict y mata
  el polling en silencio — T-BOT-2); la verificación es siempre `getMe`. El
  supervisor reinicia el polling tras `N` fallos consecutivos, sin reiniciar el
  daemon: el criterio de aceptación del hito era recuperación de polling
  verificada empíricamente.
- **Formato de mensajes**: HTML + `html.escape()` con degradación a texto plano
  (MarkdownV2 truena con contenido de TikTok — T-BOT-7); `clip()` con el sufijo
  dentro de los 4096 caracteres (T-BOT-6); rate limiter `AIORateLimiter` del
  extra `[rate-limiter]` (T-BOT-8).
- **Guards tolerantes**: un update sin `effective_chat` (channel_post, ciertos
  poll con user real) no debe reventar el guard de autorización (T-BOT-18).
- **Handlers idempotentes**: PTB re-entrega updates no confirmados tras un
  reinicio (T-BOT-4).

## 7. M6 — Operación y ronda en vivo: el día de las trampas reales

M6 cerró el dashboard estático, `system backup`, `system disk`, export, y una
**ronda en vivo** (descargas reales contra TikTok) que destapó los bugs que los
tests no alcanzaban:

- **`normalize_handle`**: persistir la URL del perfil verbatim construía
  `https://www.tiktok.com/@https://www.tiktok.com/@user`; TikTok respondía con
  redirect y el backfill terminaba `completed total=0` **silenciosamente** en una
  cuenta con vídeos. Corregido normalizando en el motor: un guard cubre los tres
  consumidores (T-ENGINE-31).
- **"has already been downloaded"**: yt-dlp salta la descarga si el archivo ya
  existe y devuelve `requested_downloads` vacío; el motor lo clasificaba como
  fallo de integridad y `retry-failed` **no convergía jamás** (17 archivos en
  disco, 17 filas churneando). Corregido adoptando el archivo por glob por id
  (T-ENGINE-32).
- **Doble trampa de ffprobe**: `--` mal ubicado dejaba opciones después del
  separador y las secciones de `-show_entries` separadas con `,` no emitían la
  sección streams → `has_video` SIEMPRE False: 17 descargas al 100% y 17 filas
  falladas. Invisible para los tests porque ffprobe estaba stubbeado (punto ciego
  del mock, hoy documentado como clase de riesgo). Corregido con el argv
  construido por una función pura + test de construcción + test de integración
  real skipif sin ffprobe (T-ENGINE-33).
- **Export UTF-8 explícito**: títulos con emoji reventaban en la consola Windows
  legacy por el codepage, después de que toda la UI ya protegida lo evitara — el
  export es dato, no UI (T-CLI-10, escrito a `sys.stdout.buffer`).

La ronda en vivo también dejó un backlog PU rechazado en el cierre: M9
(contadores de tareas zombis y drenaje en heartbeat), M10/M14 (código muerto
retirado), M13/M11/M7 (fallbacks y CLI), M16/M15/M22/M21/M23 (config, PRAGMA de
migraciones, identidad, docs, higiene determinística de tests).

## 8. La revisión encadenada final: qué detectó

Antes del cierre se ejecutó una revisión en cortes (12/12 aprobados). Hallazgos
severos, todos corregidos:

- **Backups no atómicos**: `system backup` hacía `unlink` del snapshot anterior
  **antes** del `VACUUM INTO` — un fallo a mitad de camino destruía la única
  copia buena, además de una colisión por segundo igual. Hoy: stage `*.db.tmp` +
  `os.replace`, retención corrigiendo el conteo de parciales (R7-1/2/3).
- **Arranque del bot no atómico**: pasos de lifecycle sin límite de tiempo
  podían dejar el bot a medias (R4-1/2/3) — hoy cada etapa acotada con deadline y
  el startup entero failure-atomic.
- **PRAGMA de migraciones** con orden inconsistente en conexiones de Alembic
  (M15), cooldown `0/0` no llegaba al modo desactivado del pacing (M16), y
  escapes HTML faltantes en respuestas del bot (R3-1).

## 9. Estado final

- 135 commits, de `089b6ec` (bootstrap del repo + plan) al cierre del proyecto
  (`7d3e8c2`, 12/12 reviews approved, backlog resuelto, stacks apagados) y la
  preparación para repo público que arranca en `90fd292`.
- Gate local: `ruff check`, `ruff format --check`, `pytest` — todos pasando; los
  tests marcados `live` requieren red real + TikTok alcanzable y nunca corren en
  el gate automático.
- Docker es el único entorno de producción; la guía de despliegue está en
  `docs/deployment-docker.md`.

## 10. Lecciones materializadas en diseño

Las que importan si leés esto para dirigir un proyecto similar:

1. **Clasificar el error antes de reaccionar** — la misma descarga fallida puede
   ser transitoria (extractor/WAF) o definitiva (cookie inválida, cuenta); la
   acción correcta es distinta en cada caso y un solo clasificador evita decidir
   dos veces de forma contradictoria.
2. **Una fila `[real]` del Apéndice A no se discute** — existe porque algo se
   rompió de verdad; a lo sumo se re-verifica su regla, nunca su existencia.
3. **Todo bug real corregido se registra con test de regresión y fila en el
   Apéndice A en el mismo turno** — si el registro se demora, la causa raíz se
   pierde.
4. **Un componente verificado "en aislamiento" necesita un test de que alguien lo
   llama** — sin consumidor real, el código más testeado del mundo no hace nada
   en producción (T-DATA-2).
5. **Los pines de dependencias se verifican contra las versiones reales en
   runtime, no contra etiquetas** — la nightly de yt-dlp normaliza versión por
   PEP 440 y no coincide con el tag de GitHub (T-ENGINE-20).
6. **La confianza se gana contra el sistema real, no contra los mocks** — la ronda
   en vivo de M6 destapó en una tarde los tres bugs más caros del proyecto, todos
   en caminos que los tests consideraban cubiertos.
7. **La revisión de código paga** — los dos hallazgos más severos de la ronda de
   revisión (backup destructivo del snapshot previo, startup del bot no atómico)
   pasaban todos los tests existentes.

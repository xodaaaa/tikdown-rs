# TikDown-rs — Plan Maestro (v2)

> **Naturaleza de este documento.** Es la autoridad sobre **decisiones de diseño** del proyecto: qué se
> construye, con qué forma, y qué se decidió no construir. No es un dogma: todo valor marcado como
> **hipótesis** es una medición de un entorno concreto que hay que volver a medir antes de darla por
> universal, y todo cambio de decisión se registra en el Apéndice C.
>
> **Autosuficiencia.** Este archivo es la **única** fuente de verdad del proyecto: no asume la
> existencia de ningún prototipo, versión previa, documento, discusión ni repositorio externo, y no
> remite a ninguno. Todo lo necesario está acá. Las referencias `§N`/`§N.M` apuntan a secciones de este
> mismo documento; las trampas del Apéndice A usan un **único esquema de identificador**,
> `T-<ÁREA>-<N>` (por ejemplo `T-ENGINE-7`), donde `<ÁREA>` coincide con la subsección de Apéndice A en
> la que vive (`CLI`, `ASYNC`, `DB`, `ENGINE`, `COOKIES`, `BACKFILL`, `BOT`, `DATA`, `DEPLOY`) y `<N>` es
> secuencial dentro de esa área — son **etiquetas internas estables**, citadas también en comentarios de
> código; `DR-##` son las decisiones del Apéndice C. Toda afirmación marcada como verificada lleva
> fuente y fecha en el Apéndice B; toda afirmación marcada [H] es una medición de un entorno concreto
> que hay que re-medir.

---

## 0. Cómo se usa este documento

### 0.1 Capas y autoridad

| Capa | Secciones | Qué manda |
|---|---|---|
| **Núcleo normativo** | §1–§17 | Diseño a implementar. Es la norma. |
| **Apéndice A — Trampas** | A.1–A.9 | Evidencia empírica acumulada + la regla que la neutraliza. Es **dato**, no ley: explica *por qué* la norma es como es. |
| **Apéndice B — Verificado en línea** | B | Hechos verificados con fecha y fuente, y qué hay que reverificar antes de empezar. Es **perecedero**. |
| **Apéndice C — Decisiones** | C | Cada decisión no obvia, con alternativa descartada y costo medido. Es el rastro de auditoría. |

**Marca de los valores normativos:**

- **[N]** valor fijado por diseño. Cambiarlo requiere una medición o una decisión registrada en C.
- **[H]** hipótesis empírica de un entorno concreto. Se implementa con este valor por defecto, pero
  **debe poder medirse y cambiarse sin tocar código** (variable de entorno o cadena de formato).
- **[F]** fuera del alcance base; ver §17.

**Regla de autoridad ante conflicto:** núcleo normativo > hipótesis medida en el entorno real >
suposición derivada de leer documentación de terceros. Si el entorno real contradice el núcleo, no se
ignora el núcleo en silencio: se mide, se documenta en el Apéndice C y recién entonces se cambia.

**Consumo por hito, no por turno completo.** Este documento cabe en una ventana de contexto de una sola
vez, pero después de generar el código de varios hitos la ventana de trabajo ya no tiene espacio para
sostenerlo completo con fidelidad. §12.1 divide la construcción en hitos (M0–M6) precisamente para que
cada sesión de trabajo pueda operar con un recorte: el núcleo normativo del hito en curso, más las filas
del Apéndice A cuyo `ÁREA` correspondan a ese hito, sin necesitar el resto del documento en el mismo
contexto. Esto no afecta la autosuficiencia del documento — el recorte lo decide quien lo opera en cada
sesión, el documento sigue siendo completo y autocontenido como fuente.

### 0.2 Prioridades de desambiguación (en orden)

1. Simplicidad y fiabilidad para uso homelab de un solo usuario.
2. Reutilización estricta de la capa `services/*`.
3. Fail-fast ante configuración crítica faltante (cookies, impersonación, `DATA_DIR`).
4. **Verificación empírica por encima de especificación teórica** — pero una medición aislada no se
   universaliza: se registra como [H] con su fecha y su entorno.

### 0.3 Regla de cableado

**Una capacidad cuenta como implementada solo si existe un llamador real y un test que la ejerza por su
camino de producción.** Lógica pura, testeada en aislamiento y en verde **no** cuenta como implementada.
Es el modo de fallo más caro de esta arquitectura, porque los tests verdes dan una falsa sensación de
integración: los componentes que tienden a quedar sin llamador son exactamente los que parecen
"ya resueltos" — el ciclo del monitor, el job de disco, el probe de red, el circuit breaker, el contador
de contención, el presupuesto de reintentos, el servicio de notificaciones y los eventos del catálogo.
Todo lo que este plan declara tiene que tener su llamador y su test de humo, o sale del alcance.

### 0.4 Techo de complejidad

Este documento ya está en su techo de complejidad para un proyecto de un solo usuario. Cada trampa
`[real]` del Apéndice A agregó, en su momento, una pieza propia — el circuit breaker, el contador de
contención, el dashboard, el logging JSON, la supervisión de polling — y cada una se justifica
individualmente. En conjunto, ya es la superficie máxima que este proyecto puede sostener sin dejar de
ser mantenible por una sola persona. **Regla dura para cualquier trampa que se agregue después de
completar este plan**: ninguna trampa nueva puede introducir un componente, un job, una tabla o un
archivo de configuración nuevos sin, en la misma revisión, eliminar o fusionar alguna pieza existente
que quede redundante — o demostrar explícitamente por qué ninguna lo es. Sin esta regla, el Apéndice A
crece de forma monótona y el proyecto termina por pesar más de lo que un operador de un solo host puede
sostener, que es exactamente el resultado que este plan existe para evitar.

---

## 1. Qué es el proyecto

TikDown-rs es una herramienta de **un solo usuario**, en **Python 3.13** (el sufijo `-rs` es histórico:
el proyecto **es Python**, ver Apéndice C, DR-1), para archivar vídeos de TikTok de forma automatizada y
resistente a bloqueos, controlable por terminal local y por Telegram, desplegable **exclusivamente vía
Docker**.

Dos modos del mismo código base, sin ningún servidor HTTP ni frontend:

1. **`tikdown-rs daemon run`** — proceso de larga duración (entrypoint del contenedor) con un único
   event loop asyncio: scheduler `AsyncIOScheduler` con jobs de intervalo simple, ciclo del monitor,
   validación de cookies, probe de red, chequeo de disco, heartbeat y bot de Telegram en long polling
   con supervisión activa.
2. **`tikdown-rs <grupo> <comando>`** — comandos de un solo disparo. Cada uno abre su propia conexión a
   SQLite, ejecuta lógica de `services/*` y termina. La mayoría no requiere el daemon vivo.

**Coordinación CLI ↔ daemon ↔ bot: enteramente vía SQLite en WAL.** No hay sockets, puertos ni
servidores de control.

### 1.1 Principios de diseño (8, no 18)

1. **Superficie de ataque mínima**: sin servidor HTTP, sin frontend, sin autenticación de red. Nunca se
   escucha en un puerto.
2. **Un único camino contra TikTok**: `yt-dlp` + `curl-cffi`. Nunca `httpx`/`requests` contra TikTok.
3. **Async-first con disciplina**: toda llamada bloqueante (yt-dlp, SHA-256, `ffprobe`) va a
   `asyncio.to_thread`.
4. **SQLite WAL es el bus**: estado y coordinación en tablas, no en memoria de un proceso.
5. **`services/*` es puro**: no importa `yt_dlp`, `typer` ni el SDK del bot. Es la capa que comparten
   CLI, daemon y bot.
6. **Tres categorías de fallo, nunca dos**: *definitivo*, *transitorio* e *inconcluso* (este último,
   específico de cookies). Un transitorio jamás pausa una cuenta, invalida una cookie ni consume
   reintentos.
7. **Docker es el único entorno de producción real.** Que el código sea Python puro permite ejecutarlo
   fuera de Docker, pero eso es un efecto colateral, no un objetivo: no se documenta, prueba ni mantiene
   un flujo bare-metal.
8. **Fail-fast de la operación, no del proceso** (§4.1): si falta una capacidad crítica, la operación que
   la necesita se rechaza con un error accionable; el daemon sigue sirviendo consultas y backup.

### 1.2 Alcance explícito — lo que este proyecto NO hace

Sin servidor web dinámico, sin backend con lógica de servidor, sin servidor de archivos, sin WebDAV, sin
API HTTP, sin multidominio, sin multi-usuario, sin cifrado en reposo de secrets (§15.2), sin publicación
automática de imágenes, sin CI como requisito para construir. La única excepción explícita, de alcance
muy acotado, es el dashboard estático de solo lectura de §10.3: un conjunto de archivos HTML/CSS/JS/JSON
que el propio proceso escribe a disco y nunca sirve por HTTP — no es un servidor, y toda función que no
pueda resolverse así se retira (§10.3). El resto de lo excluido está en §17 con su criterio de
activación, no en el camino crítico.

Los vídeos quedan como archivos planos en `<DATA_DIR>/videos/{username}/`. Para verlos desde otro
dispositivo, el usuario apunta un media server de terceros (Jellyfin/Plex, fuera de este proyecto) a esa
carpeta: es decisión de despliegue, no código de este proyecto.

---

## 2. Stack y política de dependencias

Todo lo de esta tabla está verificado con fecha en el Apéndice B. **Nada aquí se fija por costumbre.**

| Categoría | Elección | Notas |
|---|---|---|
| Lenguaje | **Python 3.13** [N] | `requires-python = ">=3.13,<3.14"`. El techo `<3.14` es una decisión, no una limitación de las dependencias (yt-dlp declara `>=3.10`, sin techo): **revisar en cada bump** (§B.4). |
| Paquetes | `uv` | `uv sync/lock/run`. Prerelease **solo** para el paquete que lo necesita (T-ENGINE-21). |
| CLI | `typer` + `rich` | Typer **no** tiene soporte async nativo (§B.2.3): wrappers `asyncio.run()` centralizados en `cli/common.py` (T-CLI-10). `rich` con marcadores ASCII puros (T-CLI-9, T-CLI-1). |
| DB | SQLAlchemy 2.0.x async + Alembic + `aiosqlite` | Serie `2.0.x`; `2.1.0rc1` existe pero no es estable (§B.2.2). `aiosqlite>=0.22.1` (**excluir `0.22.0`**: hang documentado, §B.2.4). |
| Config | Pydantic v2 + `pydantic-settings` | `extra` configurado para que un typo en una variable se **avise**, no se trague en silencio (T-DATA-5). |
| Descarga | `yt-dlp[default,curl-cffi,pin-curl-cffi]` — **nightly, pin exacto de fecha** [N] | Ver §2.1. |
| Impersonación | `curl-cffi`, pineado por el extra `pin-curl-cffi` de yt-dlp [N] | Nunca escribir el pin de `curl-cffi` a mano: el extra mantiene la versión exacta que esa nightly referencia (T-ENGINE-22). |
| Vídeo | `ffmpeg` / `ffprobe` (binario de sistema) | Dependencia dura: el daemon no arranca sin ellos (T-DEPLOY-10). `python:3.13-slim` **no** los trae: `apt-get install -y --no-install-recommends ffmpeg` en el stage runtime. |
| Scheduler | APScheduler 3.x (`AsyncIOScheduler`), `>=3.11,<4` [N] | 4.x sigue en pre-release y su propia doc advierte contra producción (§B.2.1). |
| Bot | `python-telegram-bot[rate-limiter]` (async, ≥22) | El extra instala `aiolimiter`: sin él `AIORateLimiter` revienta al construir el bot (T-BOT-8). |
| HTTP | `httpx` (async) | Solo Bot API y probe de red. Nunca TikTok. |
| Logging | `logging` stdlib + formatter JSON propio (`core/logging.py`) | [N] Sin `structlog`: un formatter JSON propio son ~40 líneas y evita una dependencia más; se reconsidera solo si aparece una necesidad real de contexto estructurado. Archivo rotado opcional sobre el mismo formatter. |
| Lint/test | `ruff` + `pytest` + `pytest-asyncio` | Ver §13 (política de verificación). |

### 2.1 Por qué nightly de yt-dlp (decisión, no detalle de instalación) [N]

TikTok rompe su frontend con una frecuencia tal que el canal estable de yt-dlp llega sistemáticamente
atrasado frente a los parches de extracción; el nightly los publica el mismo día. El costo de esa
decisión es real y se asume explícitamente: **actualizar yt-dlp es una operación de despliegue**
(bump del pin + `uv lock` + rebuild + selfcheck), nunca una auto-actualización en caliente.

Reglas:

- Pin **exacto con fecha** (`==AAAA.MM.DD.HHMMSS.dev0`), nunca rango abierto.
- `[tool.uv] prerelease-package = { "yt-dlp" = "allow" }`, jamás `prerelease = "allow"` global (T-ENGINE-21).
- Comparar versiones siempre contra `yt_dlp.version.__version__` (la interna coincide con el tag de
  GitHub), nunca contra la versión normalizada de PyPI (T-ENGINE-20).
- `last_known_good_ytdlp_version` en `daemon_state`: si el selfcheck falla con una versión distinta de
  la última buena, el diagnóstico por defecto es **regresión por actualización** (T-ENGINE-28).
- `YTDLP_EXTRACTOR_ARGS` como válvula de escape sin tocar código.

### 2.2 Runtime de JavaScript: no aplica al camino de TikTok

**Verificado en el fuente del extractor (yt-dlp master, 2026-09-24):** el challenge WAF de TikTok se
resuelve con una **implementación nativa en Python** — `_solve_challenge_and_set_cookies()` lee el bloque
del challenge desde el elemento `#cs` de la página y fuerza el digest esperado con un bucle de hasta
1.000.000 de iteraciones de SHA-256, con el que después fija la cookie `_wafchallengeid` (el extractor lo
informa como "Solving JS challenge using native Python implementation"). **No invoca ningún motor de
JavaScript externo** (§B.1.7).

Lo que sí exige un runtime externo es **YouTube**: desde yt-dlp `2025.11.12` la resolución de sus
challenges EJS necesita un motor, y el extra `default` incluye `yt-dlp-ejs` pero **no instala el motor**.
Runtimes soportados y mínimos documentados: `deno >= 2.3`, `node >= 22`, `quickjs >= 2023-12-9`; `bun`
queda deprecado (§B.1.7).

**Decisión [N]**: la imagen **no instala** un runtime de JavaScript. El proyecto descarga de TikTok, y ahí
el requisito no aplica; sumar `node` o `deno` a la imagen sería peso muerto y superficie extra. Dos
consecuencias que sí se implementan:

1. El bucle de PoW es **CPU-bound, de hasta 10^6 iteraciones**: corre dentro de `to_thread` (§4.3), y el
   timeout por vídeo (`DOWNLOAD_TIMEOUT_SECONDS`) tiene que absorberlo.
2. Un challenge que no se resuelve es **transitorio** y nunca debe pausar la cuenta: `Unable to solve JS
   challenge`, `Unable to extract challenge data` y el `Please wait...` de la página van a la rama
   transitoria (§4.4, T-ENGINE-30).

**Condición de revisión**: si el proyecto agrega YouTube como fuente, el runtime pasa a **requisito duro**
con los mínimos de arriba, instalado en el stage runtime del Dockerfile. El código nunca hardcodea `deno`
ni `node`: el selfcheck solo reporta si hay un runtime disponible (§4.1).

### 2.3 Procedimiento de reverificación (obligatorio antes de fijar o bumpear un pin)

**Es el primer paso de cualquier sesión de construcción, no un trámite eventual.** La combinación de
versiones de B.3 tiene fecha: para cuando se ejecute `uv lock` por primera vez, la nightly de yt-dlp y
`curl-cffi` pineadas aquí ya serán viejas. Tratar estos números como constantes fijas del documento en
vez de repetir este procedimiento hereda, el primer día, un pin desactualizado.

1. Consultar la versión publicada de cada dependencia crítica y la fecha.
2. Para yt-dlp: elegir la nightly más reciente (`YYYY.MM.DD.HHMMSS`), confirmar que su pyproject sigue
   declarando el extra `pin-curl-cffi` y qué versión de `curl-cffi` referencia.
3. Confirmar que hay wheels de `curl-cffi` para las plataformas objetivo (amd64/arm64) **antes** de
   comprometerse a ARM64 (§14.3).
4. Restricciones que no se relajan sin motivo: `aiosqlite != 0.22.0`; `SQLAlchemy >=2.0.51,<2.1`;
   `APScheduler >=3.11,<4`; prerelease acotado a yt-dlp.
5. `uv lock` + suite completa + selfcheck antes de dar el pin por bueno.
6. Registrar la fecha de verificación y cualquier cambio en el Apéndice B.

---

## 3. Modelo de datos

Base de negocio en `<DATA_DIR>/tikdown-rs.db`, gestionada con Alembic. El implementador define columnas
exactas, índices y constraints, pero debe soportar lo descrito. **Todos los estados de todos los enums
entran en el CHECK desde la migración inicial**: agregar un valor a un CHECK después es una migración
de tabla, y completar el enum después obliga a una migración de tabla (T-BACKFILL-9, T-DATA-1).

### 3.1 `monitored_accounts`

| Columna | Tipo / valores | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `username` | TEXT UNIQUE NOT NULL | sin `@` |
| `mode` | `'history'` \| `'monitor'` | default `'history'` |
| `paused` | BOOLEAN DEFAULT 0 | |
| `needs_review` | BOOLEAN DEFAULT 0 | lo pone el circuit breaker |
| `notify_on_download` | BOOLEAN DEFAULT 0 | único opt-in de notificación |
| `monitor_after_backfill` | BOOLEAN DEFAULT 0 | bandera consumible de `--then-monitor` |
| `backfill_status` | `'idle'\|'queued'\|'backfilling'\|'paused'\|'completed'\|'failed'\|'cancelled'` | **`'cancelled'` y `'paused'` desde el primer esquema** (T-BACKFILL-9). `'paused'` tiene productor real desde el día uno: la pausa automática por disco/red |
| `backfill_pause_reason` | `'disk'` \| `'network'` \| NULL | NULL si no está `paused` |
| `backfill_cursor` | TEXT | cursor por `upload_date` (§9.2) |
| `backfill_total`, `backfill_done` | INTEGER DEFAULT 0 | contabilidad de progreso (§9.3) |
| `last_check_at` | TEXT | ISO8601 UTC; throttle 30 s (§9.4) |
| `follower_count`, `following_count`, `total_likes`, `video_count` | INTEGER | refresco de perfil 48 h |
| `profile_last_refreshed` | TEXT | |
| `total_disk_bytes` | INTEGER NOT NULL DEFAULT 0 | suma acumulada de `file_size` de los vídeos descargados con éxito de esta cuenta; **incrementada por `handle_download_result`** en la misma transacción que persiste cada descarga exitosa (§4.7), nunca recalculada recorriendo el sistema de archivos (T-DATA-10); fuente del tamaño en disco por cuenta y del consumo estimado que muestra el dashboard (§10.3) |
| `created_at`, `updated_at` | TEXT | |

### 3.2 `videos`

| Columna | Tipo / valores | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `tiktok_video_id` | TEXT UNIQUE NOT NULL | |
| `account_id` | INTEGER FK → `monitored_accounts` | |
| `url`, `title`, `description` | TEXT | `url` siempre **URL de página canónica**, nunca CDN (T-ENGINE-14) |
| `duration` | INTEGER | |
| `upload_date` | TEXT | canónico `YYYYMMDD`, **nunca** mezclado con ISO8601 (T-ENGINE-25) |
| `local_path` | TEXT | absoluto, derivado de `DATA_DIR` (T-BACKFILL-21) |
| `file_size`, `file_hash` | INTEGER, TEXT | SHA-256 |
| `status` | `'pending'\|'downloaded'\|'failed'\|'cancelled'\|'skipped'` | **`'pending'` es obligatorio** — ver §3.3 |
| `downloaded_at` | TEXT | |
| `retry_count` | INTEGER DEFAULT 0 | |
| `error_message`, `error_category` | TEXT | categoría: `'definitive'\|'transient'\|'integrity'` |
| `created_at`, `updated_at` | TEXT | |

### 3.3 El ciclo de vida del vídeo descubierto

La lista de estados de §3.2 **debe incluir `pending`**, y el flujo que lo usa tiene que estar definido:
sin él no hay forma de persistir un vídeo descubierto por el monitor antes de descargarlo. Ese es un fallo silencioso concreto de este diseño: si el estado no está en el CHECK, el `INSERT`
falla, el error queda tragado como `WARNING` en el bucle del monitor y el resultado es indistinguible de
"la cuenta no subió nada" (T-DATA-1).

Flujo normativo:

1. El ciclo del monitor lista el feed (`extract_profile`) y, por cada vídeo nuevo, hace
   `INSERT ... status='pending'` con la URL de página normalizada.
2. `download_pendings()` toma los `pending`, aplica el pacing cross-proceso (§4.5) y descarga.
3. El estado terminal (`downloaded` / `failed` / `skipped`) lo escribe **siempre**
   `services/videos.handle_download_result` (§4.6), nunca el job directamente.
4. Un fallo de un vídeo **no aborta el lote**: se registra y el bucle sigue (T-BACKFILL-11).
5. `error_category='integrity'` se persiste como `status='failed'` (el CHECK de `status` no incluye
   `integrity`; la categoría vive en su propia columna).

Un estado fuera del CHECK es un fallo silencioso, no un error visible: por eso la lista de §3.2 es
cerrada y el test de migración debe ejercer el camino real contra una base con el CHECK activo
(un test con un doble falso no lo detecta).

### 3.4 `cookies`

`id`, `label`, `cookie_blob` (**LargeBinary NOT NULL**, nunca `Text`; archivo Netscape **sin cifrar**,
ver §15.2), `expiration_date`, `last_validated_at` (solo se actualiza con `valid`/`invalid`),
`validation_state` (`'valid'|'invalid'|'inconclusive'`), `last_validation_reason`, `created_at`,
`updated_at`.

### 3.5 Tablas de coordinación (singletons con `CHECK (id = 1)`)

- **`daemon_state`**: `monitor_running`, `stop_requested`, `daemon_pid`, `daemon_started_at`,
  `last_heartbeat_at`, `db_busy_count_5min`, `downloads_paused`, `last_known_good_ytdlp_version`,
  `last_selfcheck_at`, `last_selfcheck_ok`.
  - Creación idempotente `INSERT ... ON CONFLICT DO NOTHING` + relectura, **con commit inmediato**
    (T-DB-11, T-DB-6). La migración crea la tabla pero no inserta la fila.
  - `stop_requested` debe estar en `False` **al arrancar** (`_clear_stop_requested()` en `_start()`):
    un `daemon stop` de una sesión anterior, si la fila persiste, autoapaga el arranque siguiente
    (T-CLI-7).
- **`backfill_slot`**: `owner`, `acquired_at`. Adquisición CAS cross-proceso:
  `UPDATE backfill_slot SET owner=:me, acquired_at=:now WHERE id=1 AND owner IS NULL RETURNING owner`;
  liberación con `WHERE owner=:me`. Es lo que hace que el slot sea visible para daemon, CLI y bot a la
  vez, en lugar de un `asyncio.Lock` de un solo proceso (T-BACKFILL-20).
- **`download_pacing_state`**: `next_allowed_at` ISO8601 UTC con `timespec="milliseconds"` (T-DB-7,
  T-ENGINE-26). Reserva atómica con `RETURNING`. **`INSERT ... ON CONFLICT DO NOTHING` nativo, nunca
  `session.add()`** sobre una fila con PK ocupada (T-DB-12), y con commit inmediato (T-DB-6).

### 3.6 `download_archive`

Tabla espejo consultable del archivo de texto `<DATA_DIR>/download_archive.txt` (append-only, para
`--download-archive` de yt-dlp). El parser reconoce **ambos formatos de línea** (`tiktok <id>` y `<id>`
pelado): el ID es el **último token** (T-DB-8, T-DEPLOY-15).

### 3.7 Invariantes de acceso a datos

- **Orden obligatorio de PRAGMAs por conexión**: `busy_timeout` **primero**, `journal_mode=WAL`
  **después** el resto (T-DB-5). `busy_timeout=5000`, `synchronous=NORMAL`, `foreign_keys=ON`,
  `temp_store=MEMORY`, `cache_size=-65536`, `mmap_size=268435456`.
- `poolclass=NullPool` en producción; `StaticPool` sobre una URL **en memoria**, y solo en tests. "Es en
  memoria" se decide sobre la URL **parseada** —componente de base de datos vacío o `:memory:`— y nunca
  buscando `///` en el texto: `sqlite+aiosqlite:///:memory:` lleva `///` y es en memoria (T-DB-9).
- **Sesiones cortas y explícitas**; prohibido mantener una sesión abierta durante I/O de red (T-DB-15).
  Consultas sobre la sesión activa, nunca sobre el `async_sessionmaker` (T-DB-3) ni abriendo una sesión
  anidada que deje el objeto detached (T-DB-4).
- **Relaciones cargadas explícitamente** con `selectinload(...)`: el lazy load implícito no funciona en
  async y crashea al salir de la sesión (T-DB-1, T-DB-2).
- Los helpers que mutan singletons **commitean internamente** (T-DB-13).
- `DATA_DIR` debe vivir en disco local (o volumen sobre disco local): SQLite WAL no es fiable sobre
  NFS/SMB.

---

## 4. Motor de descarga (yt-dlp + curl-cffi)

### 4.1 Impersonación: qué exige el extractor y cómo se comprueba sin romperse

**Hecho verificado (2026-09-24):** el extractor de TikTok de yt-dlp pasa `impersonate=True` en sus
propias peticiones web (3 sitios en `yt_dlp/extractor/tiktok.py`). Por lo tanto `curl-cffi` con targets
disponibles es **requisito duro** para extraer de TikTok: no es una idea forzada del plan, es cómo
funciona upstream hoy (§B.1.3).

**Hecho también verificado:** el acceso programático a los targets es **API privada**
(`_get_available_impersonate_targets`), con un TODO explícito de upstream para hacerla pública; y el
parámetro `params["impersonate"]` del motor exige **objetos** `ImpersonateTarget`, no strings (T-ENGINE-6).

**Hecho empírico [H], medido en un entorno de despliegue real:** usar `impersonate` a nivel de
*parámetros del motor* empeoró el resultado — redujo el listado del feed a una sola entrada y rompió la
descarga con `Unable to extract universal data for rehydration` (T-ENGINE-11/T-ENGINE-15). La impersonación que
*importa* es la que el propio extractor aplica internamente.

Diseño normativo:

- **La sonda de capacidad tiene tres capas y no depende de una sola API.** En orden: (1) accessor
  privado si existe, normalizando defensivamente la forma de retorno (objetos vs strings, `(target,
  handler)` vs `target`); (2) `import curl_cffi` + lista de targets soportados de la librería; (3)
  `yt-dlp --list-impersonate-targets` como verificación manual documentada. **Distinguir "no está
  disponible" de "no se pudo inspeccionar"**: un cambio de forma en la API privada degrada el
  diagnóstico, no la operación.
- **Fail-fast de la operación**: si la sonda concluye "no hay impersonación posible", el daemon arranca
  en estado degradado —sirve `daemon status`, `system backup`, `cookies list`— y **rechaza**
  `monitor start` y `backfill run` con un error accionable. Registrar el estado en `daemon_state` para
  que `daemon status`/`healthcheck` lo muestren.
- **`params["impersonate"]` es opt-in, apagado, y su valor por defecto no se cambia sin medir**
  (T-ENGINE-11/T-ENGINE-15). Ver Apéndice C (DR-4).
- El selfcheck reporta las tres causas clásicas cuando no hay targets: (1) `curl-cffi` ausente;
  (2) serie incompatible con la nightly pineada; (3) limitación de plataforma (histórico en
  `linux/aarch64`). Distinguirlas es lo que evita horas de diagnóstico (T-ENGINE-22).

```python
import logging
import yt_dlp

log = logging.getLogger("tikdown_rs.verify")


def probe_impersonation() -> tuple[bool, str, int]:
    """Devuelve (disponible, motivo, cantidad_de_targets). Nunca lanza."""
    ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
    try:
        raw = getattr(ydl, "_get_available_impersonate_targets", lambda: [])()
        targets = [item[0] if isinstance(item, tuple) else item for item in raw]
        if targets:
            return True, "api-privada", len(targets)
    except Exception:  # T-ENGINE-22: la inspección puede fallar por cambio de forma
        log.warning("selfcheck.impersonation_api_changed", exc_info=True)

    try:
        from curl_cffi import requests as curl_requests  # noqa: F401

        return True, "curl_cffi-importable", 0  # importar != tener targets: ver §4.1
    except Exception:
        return False, "curl_cffi-ausente", 0
```

### 4.2 Formato de descarga [H]

Cadena de preferencia **por defecto** (medida en un entorno de despliegue real; el formato
DASH con merge separado exigía resolver el vídeo completo, que TikTok bloquea con `Unable to extract
universal data for rehydration`, mientras que el progresivo de una sola pista descargaba bien —
T-ENGINE-10):

```python
DEFAULT_FORMAT = "best[height<=1080]/best"  # progresivo primero
FALLBACK_FORMAT = "bestvideo[height<=1080]+bestaudio/best"  # DASH como último recurso
```

Reglas: `DOWNLOAD_FORMAT` (entorno) permite override global; el orden es [H] y se re-mide ante cambios
del entorno; cualquier re-medición se registra en el Apéndice C. `merge_output_format="mp4"`.

Opciones de endurecimiento del motor: `extractor_retries` 5–10 para listados, `sleep_interval_requests`
1–3 s (T-ENGINE-23), `socket_timeout` 20–30 s, `fragment_retries` sin tocar salvo motivo.

### 4.3 Cookies en el motor

- El motor recibe el **blob de cookies descifrado** (o el texto Netscape) inyectado por quien lo invoca.
  Ningún motor se construye sin cookies en una ruta que las necesite: ese patrón se rompe dos veces si la carga queda repartida — `cookies=[]` hardcodeado y motor
  construido sin cookies (T-BACKFILL-12): cargar las cookies es responsabilidad del punto de entrada, nunca un
  valor por defecto silencioso.
- Al materializarlas en disco, **el archivo Netscape reconstruido lleva el magic header
  `# Netscape HTTP Cookie File` en la primera línea** (sin duplicar si ya lo trae) y `newline="\n"`
  explícito: el parser real (`YoutubeDLCookieJar` → `MozillaCookieJar._really_load`) exige ese header y
  un parser propio tolerante **enmascara** el rechazo (T-COOKIES-1). Los tests cargan el tempfile
  regenerado con el parser **real**.
- Tempfile con `mkstemp`, **`os.close(fd)` inmediato** (en Windows un fd abierto impide `unlink`,
  T-COOKIES-7) y borrado en `finally` (T-COOKIES-6). La limpieza posterior a un éxito confirmado es **best-effort** (se registra una advertencia y el
  resultado sigue siendo éxito), pero la **ausencia** de limpieza no lo es.
- Rotación round-robin sobre las cookies válidas.

### 4.4 Clasificación de errores [N] — orden de evaluación obligatorio

Matching **case-insensitive sobre la cadena completa de la excepción, incluida su causa encadenada**,
nunca sobre subcadenas cortas aisladas.

| # | Señal | Categoría |
|---|---|---|
| 1 | Marcadores de bloqueo por IP/ritmo (`blocked`, `ip address is blocked`) | transitorio — **evaluar antes que 403 genérico** |
| 2 | Autenticación: `requiring login`, `login required`, `log into an account`, `log in for access`, `permission to view`, `account is private`, `captcha`, `banned`, `suspended`, `session expired` | definitivo |
| 3 | 403 sin evidencia de auth | transitorio |
| 4 | Contenido inexistente: `video unavailable`, `404`, `removed`, status desconocido distinto de 0 | definitivo |
| 5 | `Video not available, status code 0` — **evaluar antes que la regla 4 la capture como "status desconocido"**, es una excepción explícita a ella | transitorio (respuesta degradada/anti-bot, T-ENGINE-3) |
| 6 | Degradación del extractor o challenge no resuelto: `keeps sending the same page`, `unable to extract`, `no entries`, JSON inválido, `Unable to solve JS challenge`, `Unable to extract challenge data`, `Please wait...` (challenge WAF de TikTok, T-ENGINE-30) | transitorio |
| 7 | Errores locales de disco (`no space left`, ENOSPC) | fallo **local accionable**: `downloads_paused=1` + aviso; no cuenta para el breaker ni toca cookies |
| 8 | `does not have any videos posted` | informativo, no cuenta para nada |
| 9 | 429, timeouts, `service unavailable` | transitorio |
| 10 | Cualquier otra cosa | **transitorio** (nunca definitivo por defecto) |

Evitar marcadores demasiado amplios (`account`, `not available`): clasifican mal contenido legítimo
(T-ENGINE-2).

### 4.5 Concurrencia, pacing y rutas

- **Semáforo global de descargas** (`MAX_CONCURRENT_DOWNLOADS`, default 1) a nivel de proceso.
- **Cooldown global cross-proceso con un punto único de paso**: monitor, backfill (daemon o CLI),
  reintentos y acciones del bot comparten el reloj persistido en `download_pacing_state` (§3.5).
  **Sorteo aleatorio uniforme por descarga** en `[MIN, MAX]` (defaults 30/120 s): un intervalo fijo es
  fingerprinteable (T-ENGINE-26). `MIN=MAX` → fijo; ambos `0` → desactivado (los tests del motor lo desactivan,
  T-ENGINE-8); `MAX<MIN` → error de configuración en el arranque (T-DEPLOY-8).
- Reserva atómica con `RETURNING`; RNG inyectable para tests. Commit inmediato del singleton y
  milisegundos en el timestamp: sin eso el cooldown cross-proceso falla **en silencio y hacia
  fail-open** (T-DB-6, T-DB-7).
- **Rutas siempre derivadas de `DATA_DIR`** vía `core/paths.py`: `videos_root = <DATA_DIR>/videos`,
  `outtmpl = <DATA_DIR>/videos/%(uploader)s/%(id)s.%(ext)s` (T-BACKFILL-21). El outtmpl usa `%(id)s` **normalizado
  a la URL de página** (T-ENGINE-14).
- `--download-archive` real en **ambas** llamadas del embudo (intento normal y fallback, T-ENGINE-18).
- **Timeouts**: `asyncio.wait_for(to_thread(...))` **no mata el hilo nativo** de yt-dlp. El hilo zombi
  sigue haciendo peticiones y puede seguir escribiendo el outtmpl: todo reintento escribe a
  `.retry-N` y solo renombra tras superar integridad (T-ASYNC-15, T-ASYNC-14). El contador de hilos zombis se expone
  en `daemon status`.

### 4.6 Listado de feeds

`TikTokUserIE` pagina `api/creator/item_list` en bloques de ~15 ítems, de más nuevo a más viejo. Las
entradas ya traen metadatos completos: no hay que re-extraer cada vídeo.

Reglas obligatorias del listado:

- **`flat_playlist=True` siempre**: listar URLs, no resolver cada vídeo. Sin esto TikTok bloquea de forma
  intermitente con `Unexpected response from webpage request` (T-ENGINE-12).
- **`ignoreerrors=True` + filtrado de `None`**: con `ignoreerrors`, una entrada fallida deja un `None`
  en la lista; iterar sin filtrar produce `AttributeError` (T-ENGINE-13).
- **Sin `impersonate` a nivel de parámetros** (T-ENGINE-11/T-ENGINE-15 [H]).
- **Normalizar cada entrada a la URL de página canónica** `https://www.tiktok.com/@{username}/video/{id}`
  antes de persistirla o pasarla al motor: con cookies, yt-dlp puede devolver URLs de CDN con query
  strings gigantes y `%(id)s` revienta con `OSError: File name too long` (T-ENGINE-14).
- **`upload_date` en `YYYYMMDD`** en toda la cadena (T-ENGINE-25).
- **Una cuenta con `last_check_at IS NULL` se comprueba siempre**: tratar `NULL` como "0 segundos" hizo
  que las cuentas recién añadidas nunca se comprobaran (T-BACKFILL-1).
- `upload_date` ausente → se le asigna el cursor **anterior actualizado** (nunca `NULL`, que rompería el
  cursor de backfill; nunca un valor stale, T-BACKFILL-3).
- Detección de estados: cuenta privada (`10222`), sin vídeos (informativo), feed que repite página
  (`keeps sending the same page` → transitorio, T-ENGINE-4).

### 4.7 Integridad post-descarga — un único punto de verdad

`services/videos.handle_download_result` es el **único** camino que marca un vídeo como descargado, y lo
usan monitor, backfill y reintentos:

1. El archivo existe y su tamaño es > 0. Si no, `error_category='integrity'` explícito. Nunca
   `downloaded` sin verificar.
2. SHA-256 → `file_hash` (en `to_thread`).
3. `ffprobe` valida pista de vídeo, duración > 0, codecs y resolución (con `--` antes de la ruta, T-ENGINE-24).
4. En el mismo commit que persiste el vídeo como `downloaded`, incrementa
   `monitored_accounts.total_disk_bytes += file_size` (§3.1, soporte del dashboard de §10.3) — nunca como
   un paso separado que pueda perderse ante un fallo a mitad.

**Un archivo sin pista de vídeo tiene dos causas distintas** y confundirlas es un bug real (T-ENGINE-5):

- **Slideshow / post de fotos** (esperado): el extractor solo ofrece `vcodec='none'` → `status='skipped'`
  + alta en el dedupe, sin reintentos.
- **Respuesta degradada** (fallo real): TikTok solo expuso audio en esa respuesta → un reintento con el
  formato de fallback, descartando **antes** la entrada del archive (si no, yt-dlp responde "already
  downloaded", T-ENGINE-18). Si persiste: `error_category='integrity'`.

Firma: `handle_download_result(..., base_retry_count, expected_has_video, notify_on_download, on_event)`.

Dos contratos que se rompen con facilidad:

- **El canal de eventos es SÍNCRONO** (`on_event(...)`, sin `await`). Envolverlo en un `async def` crea
  una corrutina que nunca se ejecuta y pierde eventos en silencio (T-BACKFILL-15).
- **`on_event` se propaga explícitamente** a toda corrutina lanzada por un job: un backfill lanzado sin
  canal pierde todos sus eventos (T-BACKFILL-13).

### 4.8 Extensibilidad

`DownloadEngine` como `typing.Protocol` (`download`, `extract_profile`, `list_videos`,
`validate_cookie`); `services/*` nunca importa `yt_dlp`. **Todo método del Protocol tiene
implementación real en la clase concreta y un test de contrato que instancie esa clase y llame a cada
método**: un método declarado en el Protocol sin implementación real rompe el flujo completo con `AttributeError`
(T-ENGINE-16). Un doble de test que implementa solo lo que el test
necesita es exactamente lo que ocultó ese bug.

Nombres inequívocos: `db_engine` (SQLAlchemy) vs `download_engine` (yt-dlp), nunca `engine` a secas
(T-ENGINE-17).

---

## 5. El daemon

### 5.1 Arranque (`tikdown-rs daemon run`) — orden estricto

1. `settings.validate_for_daemon()`: fail-fast de configuración (T-DEPLOY-8).
2. `_clear_stop_requested()`: un flag heredado autoapagaría este arranque (T-CLI-7).
3. Aplicar migraciones Alembic pendientes, en `to_thread` (el `env.py` de Alembic hace `asyncio.run()`
   por dentro: llamarlo dentro del loop revienta, T-ASYNC-4).
4. **Reaplicar el setup de logging inmediatamente después**: el `fileConfig()` de Alembic pisa el root
   logger; `disable_existing_loggers=False` no alcanza (T-DEPLOY-6). `basicConfig(force=True)`.
5. Construir componentes: `NetworkMonitor`, `DownloadEngine`, `DownloadArchive`, `session factory`.
6. Estado inicial: **el monitor arranca siempre detenido** (salvo `MONITOR_AUTOSTART=true`) y
   `monitor_running=0`; reconciliaciones en este punto: transiciones pendientes history→monitor (T-BACKFILL-16) y
   `reconcile_stale_backfills()` — devuelve a `queued` lo huérfano en `backfilling` y **no toca**
   `paused` (T-BACKFILL-6).
7. Sonda de capacidad de impersonación (§4.1) y de `ffmpeg`/`ffprobe` (T-DEPLOY-10). Estado degradado visible
   en `daemon_state`.
8. Bot de Telegram si está habilitado, con dependencias inyectadas (T-BOT-3) y **supervisión activa desde el
   primer momento** (§6.4).
9. Registrar los jobs del scheduler (§5.3).
10. `await stop_event.wait()`.

**Un único `asyncio.run(_lifecycle())` para todo el ciclo de vida** (start + run + shutdown): un
`asyncio.run()` por fase crea loops nuevos, desengancha el scheduler y deja el watcher de
`stop_requested` muerto — el proceso queda zombi aunque el comando "funcione" (T-ASYNC-3).

### 5.2 Apagado (SIGTERM, SIGINT o `daemon stop`) — orden crítico

1. `stop_event` activado, por señal o por `stop_requested` detectado por el watcher.
2. Detener el *scheduling* como señal; el drenaje real lo hace el registro de tareas supervisadas:
   `AsyncIOScheduler.shutdown(wait=True)` **no espera** los jobs en curso, los cancela (T-ASYNC-9).
3. **Emitir `daemon.stopped` ANTES del drenaje** (T-ASYNC-6).
4. Bot: `updater.stop() → stop() → shutdown()`.
5. Señal de parada al motor; máximo 10 s para terminar entre fragmentos.
6. Descargas pendientes → `cancelled` (no terminal para el cursor, no entra al dedupe, T-BACKFILL-10). El backfill
   vuelve a `queued`, o a `paused` si la interrupción coincide con disco lleno o red caída (§9.1).
7. Drenaje (10 s) y cancelación explícita de las tareas supervisadas con referencias `Task` reales
   (T-ASYNC-11), indexadas por `id(task)` y no por nombre (T-ASYNC-12).
8. Limpieza de `daemon_state` (`daemon_pid`, `last_heartbeat_at`, `monitor_running=0`) y dispose del
   engine.

### 5.3 Jobs del scheduler (jobstore en memoria, no persistido)

`SQLAlchemyJobStore` serializa con pickle y falla con bound methods; todos los jobs son de intervalo
simple y se recrean determinísticamente en cada arranque: el único estado que sobrevive es el de negocio
en SQLite (DR-6). Todos los jobs de ciclo largo llevan `max_instances=1` + `coalesce=True`
(T-ASYNC-13).

| Job | Intervalo | Qué hace | Visible sin notificaciones |
|---|---|---|---|
| `heartbeat` | `HEARTBEAT_INTERVAL_SECONDS` (10 s) | Escribe `last_heartbeat_at` y **lee `monitor_running`** para arrancar/drenar el ciclo del monitor en caliente (T-CLI-6) | `daemon status` |
| `disk-check` | 900 s | Espacio libre; bajo umbral → `downloads_paused=1` + aviso; reanudación automática al recuperar (T-ENGINE-27/T-DATA-9) | `system disk` |
| `network-probe` | 30 s | Máquina `online`/`offline` (§8) | heartbeat/logs |
| `backfill-collect` | 60 s | Recoge backfills `queued` **y `paused` reanudables** si el slot está libre; propaga `on_event` (T-BACKFILL-13) | `backfill status` |
| `cookies-validate` | 6 h | Validación secuencial, 30–60 s entre sondas (§7) | `cookies list` |
| `profile-refresh` | 48 h | Refresco de contadores de perfil | `accounts stats` |
| `selfcheck` | 24 h | Sonda de impersonación + `ffmpeg` + `DATA_DIR`; detecta regresión post-bump | `daemon status` |

**Regla de no-huérfanos**: todo job de esta tabla debe dejar un rastro **consultable** aunque las
notificaciones estén apagadas (una columna de estado, un log estructurado, o el heartbeat). El caso típico es un job con lógica completa, tests propios y **sin ningún llamador**: el ciclo del
  monitor —la funcionalidad principal del producto— es el que más tarda en cablearse (T-DATA-2). Un job que depende de una bandera de `daemon_state` la **relee en cada
ejecución**, nunca la cachea al registrar (T-ASYNC-16).

El heartbeat actúa como watcher de control: el comando `daemon stop` no tiene efecto si nadie relee
`stop_requested` activamente. Escribir la bandera en la base no basta (T-CLI-6).

### 5.4 Migraciones idempotentes

- Comprobar primero si existe `alembic_version` (T-DEPLOY-1): sin eso se repite `stamp` en cada comando.
- **`stamp` nunca apunta a `head` directamente** — apunta a la revisión exacta en la que la migración de
  este proyecto introdujo su tabla marcadora (`daemon_state`), una constante fija
  (`MARKER_TABLE_REVISION`, el id de esa migración), **seguido siempre de `command.upgrade(alembic_cfg,
  "head")` en el mismo paso**. Este orden — `stamp(MARKER_TABLE_REVISION)` y luego `upgrade("head")` —
  es el que cierra el hueco que un `stamp("head")` ingenuo dejaría abierto: si la base ya tiene la tabla
  marcadora pero es anterior a una migración posterior (una columna añadida después), un `stamp("head")`
  la daría por al día sin haber aplicado esa migración, y el síntoma aparecería mucho después, en la
  primera escritura que dependa de la columna faltante (T-DB-10). Con `stamp(MARKER_TABLE_REVISION)` +
  `upgrade("head")`, cualquier migración posterior a la creación de la tabla marcadora se aplica siempre,
  sin excepción, porque `upgrade` corre igual en los dos caminos.
- Una base con tablas ajenas y sin `alembic_version`, y **sin** la tabla marcadora de este proyecto,
  recibe `upgrade("head")` directo, sin ningún `stamp` — es una base nueva desde el punto de vista de
  este proyecto.
- `command.upgrade(alembic_cfg, "head")` en cualquier otro caso.
- Lock de migración cross-proceso en `<DATA_DIR>/.migrate.lock` (T-DEPLOY-3).
- Exención de sondeo: `daemon healthcheck` y `--version` **no** ejecutan migraciones ni toman el lock.
- Localización de `alembic.ini`/`alembic/` **por candidatos con error explícito** (junto al módulo en dev
  editable; cwd en la imagen). Nunca `Path(__file__).parents[1]` a secas: en un wheel instalado apunta a
  `site-packages`, donde no hay `alembic.ini` (T-DEPLOY-4).
- `env.py` con template async (`connection.run_sync`): el default síncrono no sirve con `aiosqlite`
  (T-DEPLOY-2). Logger `alembic` a `WARNING`.
- **Todo artefacto de despliegue que dependa de recursos no-Python los copia explícitamente**: el stage
  runtime de la imagen debe contener `alembic.ini` y `alembic/versions/` (T-DEPLOY-5).

### 5.5 Tareas supervisadas

- **Toda tarea de fondo pasa por `create_supervised_task()`**; nunca `asyncio.create_task` directo en
  ningún módulo.
- El `add_done_callback` debe ser **síncrono**: un callback `async` crea la corrutina y nunca la ejecuta
  (T-ASYNC-1). Interpolar el nombre en el mensaje del log; `logging` no acepta `name=` como kwarg (T-ASYNC-2).
- `daemon status` expone las tareas supervisadas activas y los hilos zombis de yt-dlp (T-ASYNC-14).

### 5.6 Observabilidad de contención SQLite

Listener `handle_error` a nivel de engine que captura `database is locked` e incrementa un contador en
memoria; el heartbeat persiste una **ventana rotativa real de 5 minutos** en
`daemon_state.db_busy_count_5min`; por encima de `DB_BUSY_TIMEOUT_ALERT_THRESHOLD` se registra
`daemon.db_contention` con dedupe por flanco. `daemon status`/`healthcheck` leen el contador **desde
`daemon_state`**, nunca desde el proceso CLI (que por definición siempre ve 0, T-DB-14).

Alcance real de este punto: es pequeño a propósito (contador + una columna + una lectura). Si no se
puede cablear end-to-end en el hito M4, se retira del alcance en lugar de quedar como lógica muerta
(§0.3).

### 5.7 Logs a archivo rotado (opcional, sobre stdout)

El JSON a stdout se mantiene **siempre**; el archivo rotado es una capa adicional de retención local:
`LOG_FILE_PATH` (vacío = solo stdout), `LOG_FILE_MAX_BYTES` (10 MB), `LOG_FILE_BACKUP_COUNT` (7),
`LOG_FILE_WHEN` (`size` | `midnight`). El handler de archivo usa el **mismo** `JsonFormatter`. Como la
reaplicación de logging post-migración (§5.1 paso 4) usa los mismos campos de `Settings`, el archivo
sobrevive automáticamente.

### 5.8 Auto-detención por falta de cookies

Si la última cookie válida expira, el monitor se detiene globalmente (`monitor.stopped_no_cookies`); el
backfill aborta al iniciar con `backfill.no_cookies`.

---

## 6. Bot de Telegram

### 6.1 Integración en el loop del daemon

`getUpdates` con `timeout=25` en el mismo event loop. **Nunca `run_polling()`** dentro de un loop
existente: `await app.initialize(); await app.start(); await app.updater.start_polling(timeout=25)`, y
en el apagado `updater.stop() → stop() → shutdown()` (T-BOT-1).

**Nunca verificar el bot con un `getUpdates` manual**: la Bot API admite una sola sesión de `getUpdates`
por bot, así que una llamada de diagnóstico mata el polling en curso con `Conflict` (409) y el bot queda
muerto en silencio. Verificar siempre con `getMe`/`sendMessage` (T-BOT-2).

El bot crea **un único** engine async al arrancar, inyectado por el daemon en el constructor, y un flag
`owns_engine` decide si lo dispone al terminar (T-BOT-3).

PTB mantiene el offset en memoria: tras un reinicio puede reentregar updates no confirmados, por lo que
**todos los handlers son idempotentes** (T-BOT-4).

### 6.2 Alcance del bot en este plan

Comandos planos (Telegram no tiene subcomandos anidados): `/start /help /list /add /stats /disk /status
/monitor /check /backfill /pause /resume /remove /cookies /notify /last`. La paridad con la CLI es
**funcional** (misma función de `services/*`), no textual. El dispatcher **solo orquesta**.

**El envío push de notificaciones queda fuera del alcance base** [F] (§17.1). Lo que sí es base es el
**contrato** que abarata cablearlo después, porque tenerlo a medias cuesta más que no tenerlo: servicio noop, tabla de spool sin lecturas ni
escrituras, eventos sin productor y variables de entorno sin efecto:

- `core/notifications/events.py` con el **catálogo de plantillas** y una función `render(event, payload)`.
- Un canal **síncrono** de emisión inyectado en los jobs, con `NoopNotificationService` por defecto.
- El spool persistente y el envío real son parte de la épica futura.

### 6.3 Seguridad y throttle

- **Autorización de doble capa**: `TELEGRAM_CHAT_ID` + `from_user.id` (lista opcional vía
  `TELEGRAM_USER_ID`). Se aplica en comandos, callbacks inline **y documentos subidos**.
- Guard tolerante a updates sin `effective_chat` (`None`) sin reventar (T-BOT-11).
- Intento no autorizado → `bot.unauthorized_attempt`; ≥5 intentos/5 min del mismo `from_user.id` →
  `bot.unauthorized_attempts_burst` con conteo (contador en memoria del proceso, misma limitación de
  reinicio que el breaker de cuentas).
- Throttle 1 comando / 2 s por `chat_id`, **también en callbacks**, y `query.answer()` siempre para
  cerrar el spinner.
- Botones inline con expiración **real** (timestamp validado, 60 s).
- Upload de cookies: límite 10 MB verificado por metadato remoto **y** tamaño real post-descarga.
- Tempfiles con `mkstemp`, `os.close(fd)` inmediato (T-COOKIES-7) y borrado garantizado en `finally`.
- **`callback_data` ≤ 64 bytes** (límite crudo de la API) con encoding compacto presupuestado (T-BOT-5).
- **4096 caracteres por mensaje**: `clip()` compartido, con el sufijo **dentro** del límite (T-BOT-6).
- **`parse_mode=HTML` + `html.escape()`** sobre todo contenido dinámico; MarkdownV2 prohibido;
  degradación a texto plano ante `can't parse entities` (T-BOT-7).
- `AIORateLimiter(max_retries=3)` (T-BOT-8).
- Toda interpolación de username usa `lstrip('@')`; **las plantillas ya incluyen el `@`** y el render no
  lo duplica (T-BOT-9, T-BOT-10).

### 6.4 Paginación real de `/list`

`render_list_page(accounts, page=0, page_size=5)` (lógica pura, testeable): bloque de `page_size`,
devuelve `(texto, page, total_pages)`, clamp de página fuera de rango, `total_pages = ceil(n/page_size)`,
lista vacía → `"No hay cuentas"`, escape HTML.

`build_list_keyboard(page, total_pages)`: botones ◀️/▶️ con `callback_data` compacto
`"listp:{ts}:{page}"` (≤64 bytes) y expiración real de 60 s validada en el callback; botón deshabilitado
en los extremos.

`CallbackQueryHandler` para el prefijo `listp:`: authz doble (tolerante a update sin chat) → throttle 2 s
compartido con los comandos → `query.answer()` → validar expiración → renderizar y editar el mensaje.

### 6.5 Supervisión del polling y reconexión automática

En PTB ≥20 los errores de `get_updates` pueden **no** llegar de forma fiable a `add_error_handler`: la
vía robusta es un healthcheck periódico con **`getMe`** (nunca `getUpdates`, T-BOT-2). Tarea de fondo
**supervisada** que cada `POLLING_HEALTHCHECK_INTERVAL` (30 s) llama `getMe`; tras
`POLLING_HEALTHCHECK_MAX_FAILURES` (3) fallos consecutivos reinicia el bot completo (`stop()` +
`start()`) **sin reiniciar el daemon**. Flag `_restarting` para evitar reinicios concurrentes; si el
reinicio falla, se reintenta en el ciclo siguiente.

**Aviso de verificación:** este comportamiento depende de la versión de PTB pineada. Verificar
empíricamente la vía de detección (matar el polling y observar si la supervisión lo recupera) antes de
declararlo cumplido, como ya advirtió la investigación previa sobre PTB ≥20.

---

## 7. Cookies

- Import por CLI (vía preferente) o archivo subido al bot; detección de formato (Netscape / JSON lista /
  cookie-string) y conversión **siempre** a Netscape canónico.
- **Privacidad**: documentar en el README que la vía recomendada para cookies sensibles es la CLI; si se
  usa el bot, `deleteMessage` best-effort tras importar + borrado del tempfile.
- Persistencia inmediata del blob en la base (§3.4, sin cifrar, §15.2); borrado del archivo fuente
  **best-effort** (T-COOKIES-8) con `--keep-source` para conservarlo.
- Advertir (no rechazar) si falta `sid_tt`.
- **Validación en tres estados**: solo auth **confirmado** → `invalid`; cualquier otro resultado
  (extractor, red, timeout, `no entries`) → `inconclusive` y **no toca** `validation_state` ni
  `last_validated_at` (T-COOKIES-3).
- `get_working_cookie()`: cookies `valid` ordenadas por `last_validated_at` desc; revalida si el chequeo
  es antiguo; **solo rechaza ante `invalid`** — un `inconclusive` conserva la cookie con log informativo
  (T-COOKIES-4). Un rechazo indebido acá deja el sistema sin cookies **teniendo una cookie perfecta**.
- Clamp de expiraciones absurdas al año 2100 antes de `datetime.fromtimestamp` (T-COOKIES-5). Countdown
  dedicado (positivo si futuro, 0 si pasado): no reutilizar el helper inverso.
- Sesiones cortas durante la validación: leer blob → cerrar sesión → validar (llamada de red) → reabrir
  solo para persistir (T-DB-15).
- **La sonda de validación**: perfil real, público, activo, sin restricción regional, con embedding
  habilitado, verificado con una extracción real antes de desplegar. **Itera las primeras
  `COOKIE_PROBE_MAX_ENTRIES` (5) entradas** buscando formatos de vídeo: solo si ninguna los tiene
  devuelve `inconclusive` (la primera entrada puede ser un slideshow solo-audio incluso en perfiles
  buenos, T-COOKIES-2).
- **`COOKIE_VALIDATION_URL` es una lista, no un único perfil, y no tiene valor por defecto**:
  1. *Fallback*: si el perfil activo devuelve contenido inexistente o degradación estructural, el ciclo
     pasa a la siguiente candidata y solo declara `cookie.validation_probe_failed` cuando fallan todas.
  2. *Rotación entre ciclos*: cada ciclo rota cuál candidata usa como sonda primaria, repartiendo el
     tráfico de sondeo en vez de concentrarlo en una cuenta ajena.
  El README documenta que cada despliegue configure **2–3 perfiles-sonda propios verificados**.
  **Prohibido hardcodear un perfil de terceros como default** (DR-5). Sin configurar es una configuración incompleta, no un
  default silencioso.
- **La sonda rota nunca invalida cookies**: contenido inexistente en la sonda, o `inconclusive` para
  todas las cookies a la vez, se registra como `inconclusive` global sin tocar ningún `validation_state`
  (T-COOKIES-3).

---

## 8. Red y disco

### 8.1 Red

- Máquina de estados `online`/`offline` con probe HEAD (`httpx`, timeout `NETWORK_PROBE_TIMEOUT_SECONDS`)
  a endpoints **neutrales** (`NETWORK_PROBE_URL`, nunca TikTok).
- `NETWORK_OFFLINE_THRESHOLD_CONSECUTIVE_FAILURES` (2) fallos consecutivos → `offline`,
  `network_available.clear()`. Backoff 30 s → techo 120 s con jitter. Primer probe exitoso → `online`.
- **Capturar la duración de la caída antes de limpiar `offline_since`** y notificar `network.online`
  solo si el estado inmediatamente anterior era offline **confirmado**: un blip no genera una
  notificación engañosa (T-BOT-14).
- `network_available` (`asyncio.Event`) se **inyecta**, nunca es un singleton global implícito, y se crea
  **ya seteado**: sin monitor, la red se asume disponible (si no, la primera descarga se cuelga para
  siempre, T-ENGINE-7).
- Efectos de la caída: pausa de monitor, backfill y descargas; el spool (cuando exista, §17.1) se drena
  en la transición a online confirmado.
- **Un fallo de red nunca produce `validation_state='invalid'` ni consume reintentos** (T-BACKFILL-17).

### 8.2 Disco

- Chequeo periódico (900 s) con `DISK_WARNING_FREE_PERCENT` (10 %): bajo umbral → aviso + registro.
- ENOSPC en una descarga → **fallo local accionable**: `downloads_paused=1`, alerta, y **no** cuenta para
  el circuit breaker ni toca cookies (T-ENGINE-27).
- Reanudación automática cuando el espacio vuelve por encima del umbral, o manual con
  `system disk --resume`.
- El chequeo contabiliza también el directorio de backups.

---

## 9. Backfill y monitor

### 9.1 Reglas de concurrencia

- **Cookies obligatorias**: `get_working_cookie()` al iniciar; aborta con `backfill.no_cookies` si no hay
  ninguna. Se aplica en monitor, backfill y CLI foreground .
- **Slot único cross-proceso real** (§3.5): nunca un `asyncio.Lock` por proceso. Dos backfills —uno del
  CLI y otro del daemon— solapados sobre la misma cuenta son un riesgo real de duplicar peticiones y
  disparar el anti-bot, no un caso teórico.
- El daemon recoge automáticamente backfills `queued` **y `paused` reanudables**: antes de crear la tarea
  comprueba el slot y **propaga el canal de eventos** (T-BACKFILL-13).
- **Estados no terminales para el cursor**: `queued` y `paused`. `cancelled` tampoco es terminal.
- **`paused` con productor real**: `run_backfill` distingue la causa en `except asyncio.CancelledError`
  (que es `BaseException`, no `Exception`): si coincide con `downloads_paused=1` o red offline →
  `paused` + `backfill_pause_reason`; si no hay causa identificable (crash, apagado limpio) → `queued`
  (T-BACKFILL-6).
- `collect_queued_backfills` recoge `paused` **solo si la causa se resolvió**. `reconcile_stale_backfills()`
  no toca `paused`, solo `backfilling` huérfanos.
- **Throughput esperado** (comunicarlo al usuario antes de empezar): ~40–150 s por vídeo con los
  defaults; 1.000 vídeos ≈ 11–42 h. Reanudable por diseño.

### 9.2 Cursor

- Cursor estricto por `upload_date` con comparación **estrictamente `<`**, nunca `==`.
- `scope_cursor` (snapshot para el `break`) **separado** del cursor móvil: usar el móvil en el `break`
  hacía que el backfill parara tras el primer vídeo (T-BACKFILL-2).
- El cursor **solo avanza en estado terminal** (`downloaded`/`failed`/`skipped`), nunca con `cancelled`.

### 9.3 Contabilidad de progreso

`backfill_total` se persiste **después de listar el feed real**, al iniciar cada pasada: calcularlo sobre
la variable que todavía contiene `None` dejaba el total en 0 (T-BACKFILL-5). `backfill_done` cuenta todo
vídeo en estado terminal, incluido `skipped`.

### 9.4 Cancelación real

`backfill cancel` marca `cancelled`; el worker **relee el estado periódicamente** durante la ejecución.
La persistencia de progreso es un **UPDATE condicional** con `WHERE backfill_status='backfilling'`:
`rowcount 0` significa cancelación detectada y no se sobrescribe nada (T-BACKFILL-10). Tras la cancelación
cooperativa, retorno temprano sin transición `--then-monitor` ni `backfill.completed` (T-BACKFILL-8).

### 9.5 Transición automática history → monitor (`--then-monitor`) [N]

Debe ser **end-to-end automática, sin intervención manual**: en la **misma transacción** que marca el
backfill `completed`, y con condición idempotente (`WHERE mode='history' AND monitor_after_backfill=1
AND backfill_status='completed'`): (a) la cuenta pasa a `mode='monitor'`; (b) si
`daemon_state.monitor_running` está en `False`, esta misma transición lo pasa a `True` con el mismo
helper con commit interno que usa `monitor start`; (c) `monitor_after_backfill` se consume (a 0). Solo
desde `completed`, nunca desde `failed`/`cancelled`.

`MONITOR_AUTOSTART=false` sigue aplicando **al arranque en frío del daemon**; no aplica a esta
transición, que es exactamente lo que el usuario pidió al usar el flag. Dejar el monitor detenido obliga
a una intervención manual que el flag existía para evitar (T-BACKFILL-18).

La misma `UPDATE` idempotente se aplica defensivamente en cada arranque (T-BACKFILL-16). El heartbeat recoge el
cambio y registra el job del ciclo en caliente: no hace falta reiniciar el daemon.

### 9.6 Reintentos y circuit breaker

- **Circuit breaker por cuenta**: 5 fallos de **auth** consecutivos → `paused + needs_review`. Los
  transitorios no cuentan. Contador en memoria del proceso (se resetea al reiniciar; las pausas
  persisten en la base).
- **Ronda de reintentos**: el backfill envuelve **cada** descarga en su propio `try/except`; un vídeo
  fallido se registra y el lote continúa. Un fallo transitorio de un vídeo **nunca** aborta el feed
  completo (T-BACKFILL-11).
- Techo de reintentos y presupuesto total por vídeo: **fuera del alcance base** [F] (§17.2). Razonamiento: sin un bucle de reintento automático no hay nada que acotar — el techo y el presupuesto
  solo tienen sentido cuando ese bucle exista. Se implementan junto con la
  épica de reintento automático.
- `retry-failed` usa el **mismo** `handle_download_result`, con descarte de la entrada del archive antes
  de cada reintento (T-ENGINE-18).
- El backfill descarga con cookies reales cargadas en cada punto de entrada: la CLI pasaba `cookies=[]`
  hardcodeado y **todo** backfill abortaba con `no_cookies` aunque hubiera cookies válidas (T-BACKFILL-12).
- `--queue` en `backfill run` re-encola un backfill terminado (`completed`/`failed` → `queued`) y
  **rechaza** re-encolar uno en `backfilling`: sin esto, reintentar exige resetear la base a mano
  (T-BACKFILL-19).

---

## 10. CLI

### 10.1 Superficie

Exactamente **7 grupos de sustantivo**: `daemon`, `monitor`, `accounts`, `backfill`, `cookies`,
`videos`, `system`. Sin comandos-verbo sueltos en la raíz. Pares antónimos simétricos
(`pause`/`resume`); el verbo de borrado es siempre `remove`.

| Grupo | Comandos |
|---|---|
| `daemon` | `run`, `stop`, `status`, `selfcheck`, `healthcheck` |
| `monitor` | `start`, `stop` |
| `accounts` | `add @user [--mode history\|monitor] [--then-monitor]`, `list`, `pause`, `resume`, `notify --on/--off`, `remove`, `check`, `stats` |
| `backfill` | `run @user [--queue]`, `status @user`, `cancel @user`, `retry-failed @user \| --all` |
| `cookies` | `add <ruta> [--keep-source]`, `list`, `test <id>`, `remove <id>` |
| `videos` | `last [N]`, `export [--format json\|csv]`, `integrity [username]` |
| `system` | `disk [--resume]`, `backup`, `site render` |

**Regla de registro (T-CLI-5, proceso)**: cada comando de esta tabla corresponde a un `@app.command()`
**real y alcanzable**, verificado con un test de humo `tikdown-rs <grupo> <comando> --help`. La tabla es
la especificación; el árbol de typer debe coincidir 1:1 antes de dar por completa cualquier story de
CLI. El comando `daemon run` es el `CMD` del contenedor: si existe como función pero no está registrado,
el contenedor entra en crash-loop con `No such command 'run'` desde el primer arranque. **El `Dockerfile`/`CMD` y el subcomando que invoca se verifican juntos, como una
unidad.**

`daemon status` muestra: heartbeat, monitor, resultado del último selfcheck, tareas supervisadas, hilos
zombis de yt-dlp, contador de contención (leído de `daemon_state`), cookies por estado, disco y últimos
errores derivados de la tabla `videos` (**sin tabla nueva**).

`daemon healthcheck` (para `HEALTHCHECK` de Docker) es **ligero y sin red**: frescura de heartbeat
(≤ 3× intervalo) + umbrales binarios de cookies/disco. **No ejecuta migraciones ni toma el lock**.

### 10.2 Implementación

- `typer` no tiene async nativo: wrappers síncronos con `asyncio.run(...)` centralizados en
  `cli/common.py`, junto con `run_or_exit()` (los errores de negocio salen como `ERROR <mensaje>` +
  exit 1, sin traceback, T-CLI-4) y `prepare_invocation()` (migraciones + `Settings` fresca por
  invocación, §5.4).
- `@app.callback()` global con `--version` e `invoke_without_command=True`: sin él,
  `tikdown-rs --help` lanza `RuntimeError: Could not get a command for this Typer instance` (T-CLI-2).
- Salida **ASCII puro**: los glifos Unicode revientan en consolas Windows legacy con
  `UnicodeEncodeError` (T-CLI-1); los campos propios de una barra de progreso se acceden con `{task.fields[clave]}` (corchetes) y con nombres que no colisionen — `total` y `completed` están reservados por la librería (T-CLI-9). Exportaciones sin markup ni wrap de Rich (T-CLI-3); CSV con `csv` de stdlib y
  sanitización de fórmulas (T-DEPLOY-16).
- **Regla de oro**: la lógica real vive en `services/*`; `cli/` y el dispatcher del bot **solo orquestan**.

### 10.3 Panel estático de estadísticas (dashboard)

**Qué es y qué NO es**: `system site render` genera un conjunto de archivos **estáticos** — HTML + CSS +
JS que corre enteramente en el navegador del visitante + varios JSON de datos — en `STATIC_SITE_DIR`
(por defecto `<DATA_DIR>/public`). **No es un servidor web**: el proceso de TikDown-rs nunca abre un
puerto ni sirve HTTP, solo escribe archivos a disco en cada regeneración. Si el operador quiere verlos
remotamente, apunta cualquier servidor de archivos estático externo (nginx, Caddy, un bucket con hosting
estático — todos fuera del alcance de este proyecto) a esa carpeta. El JS embebido puede filtrar,
buscar y ordenar tablas **en el navegador**, sobre los JSON ya generados — eso es trabajo del cliente,
no una petición nueva al servidor, así que sigue siendo, en su totalidad, un conjunto de archivos
estáticos sin ninguna lógica de servidor.

**Regla de admisión de cada función (aplicada a cada punto de abajo)**: si una función no puede
resolverse con datos ya calculados en el momento de generar (o con filtrado/orden puramente en el
navegador sobre esos datos), se retira sin excepción — nunca se convierte en una llamada a un endpoint
ni se añade un mini-servidor "solo para eso".

**Estructura: 4 columnas**

1. **Resumen general**: espacio en disco (libre/usado, umbral, si `downloads_paused` está activo);
   estado del monitor (`monitor_running`, intervalo, cuántas cuentas en modo `monitor`); versión de
   yt-dlp en uso y si hay una más nueva detectada (§8, sin botón de "actualizar" — actualizar yt-dlp es
   un cambio de pin + rebuild, una operación de despliegue, no algo que un sitio estático deba disparar);
   estado de cookies (`valid`/`invalid`/`inconclusive`, expirando pronto); marca de tiempo de la última
   generación.
2. **Cuentas y consumo**: tabla de cuentas (username, modo, pausada/`needs_review`, vídeos descargados,
   tamaño total en disco, progreso de backfill); un resumen agregado de bytes descargados por cuenta
   como aproximación del tráfico de red consumido (documentado explícitamente como aproximación: no
   incluye reintentos fallidos ni el tráfico de listar el feed); búsqueda/filtrado por cuenta, resuelto
   en el navegador sobre el JSON ya embebido.
3. **Historial de descargas**: últimas `STATIC_SITE_HISTORY_LIMIT` descargas (default 500 — un histórico
   sin techo infla el JSON de cada regeneración; para estadísticas de **todo** el histórico se muestran
   agregados por cuenta y por estado, no las filas completas); conteo de fallos por `error_category`;
   búsqueda/filtrado por cuenta o por fecha sobre ese histórico acotado, en el navegador.
4. **Documentación**: referencia de comandos generada por introspección del árbol de typer en el momento
   de renderizar (coincide por construcción con §10.1, nunca se mantiene a mano) + un resumen corto de
   qué es el proyecto y cómo operarlo.

**Tamaño por cuenta — decisión de diseño obligatoria (T-DATA-10)**: el tamaño no se calcula recorriendo
el sistema de archivos en cada regeneración (lento y no escala con una biblioteca de miles de vídeos).
`monitored_accounts` incorpora `total_disk_bytes INTEGER NOT NULL DEFAULT 0` (§3.1), incrementado por
`handle_download_result` en la misma transacción que persiste cada descarga exitosa (§4.7); el panel
**solo lee** esa columna, nunca recalcula.

**Qué NUNCA pertenece a este panel**: cualquier acción que module estado — importar/editar cookies,
forzar una actualización de yt-dlp, pausar/reanudar cuentas, lanzar o cancelar un backfill, iniciar/
detener el monitor — permanece exclusivamente en la CLI y el bot de Telegram. Ninguno de los JSON
generados incluye jamás cookies, tokens, ni variables de `.env`.

**Configuración**: `STATIC_SITE_ENABLED` (default `false`), `STATIC_SITE_DIR` (default
`<DATA_DIR>/public`), `STATIC_SITE_INTERVAL_MINUTES` (default 15, job opcional del scheduler activo solo
si `STATIC_SITE_ENABLED=true`), `STATIC_SITE_HISTORY_LIMIT` (default 500). El directorio de salida es
configurable a una ruta distinta de `DATA_DIR` si el operador prefiere separar el volumen que expondría
el servidor estático del volumen con datos sensibles (cookies, base de datos) — **nunca deben ser el
mismo volumen si el panel se sirve realmente al exterior**.

**Implementación**: `services/static_site.py::render_site(session, settings, output_dir)` agrega las 4
columnas (reutiliza `services/status.py` para la Columna 1; consultas de solo lectura para 2-3;
introspección del árbol de typer para la 4) y escribe `index.html` + `accounts.json` + `downloads.json`
+ `data.json`. `cli/system.py::site render` lo invoca bajo demanda.

---

## 11. Configuración

### 11.1 Variables (todas con efecto real — si una no está cableada, se retira)

```env
DATA_DIR=/app/data
LOG_LEVEL=INFO
LOG_FILE_PATH=
LOG_FILE_MAX_BYTES=10485760
LOG_FILE_BACKUP_COUNT=7
LOG_FILE_WHEN=size

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_USER_ID=
POLLING_HEALTHCHECK_INTERVAL=30
POLLING_HEALTHCHECK_MAX_FAILURES=3

MONITOR_INTERVAL_MINUTES=5
MONITOR_AUTOSTART=false

MAX_CONCURRENT_DOWNLOADS=1
GLOBAL_DOWNLOAD_COOLDOWN_MIN_SECONDS=30
GLOBAL_DOWNLOAD_COOLDOWN_MAX_SECONDS=120
DOWNLOAD_TIMEOUT_SECONDS=600
DOWNLOAD_FORMAT=
YTDLP_ANTIBOT_BACKOFF_BASE_SECONDS=10
YTDLP_ANTIBOT_BACKOFF_CEILING_SECONDS=120
YTDLP_PROXY_URL=
YTDLP_EXTRACTOR_ARGS=

COOKIE_VALIDATION_URL=
COOKIE_PROBE_MAX_ENTRIES=5

NETWORK_PROBE_URL=
NETWORK_PROBE_INTERVAL_SECONDS=30
NETWORK_PROBE_TIMEOUT_SECONDS=5
NETWORK_OFFLINE_THRESHOLD_CONSECUTIVE_FAILURES=2

HEARTBEAT_INTERVAL_SECONDS=10
DISK_WARNING_FREE_PERCENT=10
DISK_CHECK_INTERVAL_SECONDS=900
DB_BUSY_TIMEOUT_ALERT_THRESHOLD=20
SYSTEM_BACKUP_RETAIN_COUNT=7

STATIC_SITE_ENABLED=false
STATIC_SITE_DIR=
STATIC_SITE_INTERVAL_MINUTES=15
STATIC_SITE_HISTORY_LIMIT=500
```

**Fail-fast al arrancar**: intervalos inválidos; `MAX < MIN` en el cooldown; `DATA_DIR` no escribible;
modo del bot habilitado sin `TELEGRAM_CHAT_ID`. Y **aviso por variable desconocida**: un typo en una
variable de entorno se ignoraba en silencio y el usuario creía tener una configuración activa que no
aplicaba. El aviso se deriva de los prefijos reales de `Settings`, no de una lista fija hecha a mano:
una lista manual queda desactualizada en cuanto se agrega un campo, y puede no cubrir ni los nombres que
el propio código usa.

### 11.2 Constantes no configurables

| Concepto | Valor |
|---|---|
| Timeout por vídeo | `DOWNLOAD_TIMEOUT_SECONDS` (600 s) |
| Apagado: tiempo para descargas/jobs en curso | 10 s |
| Polling de `stop_requested` | ≤ 0.5 s, y ≤ 5 s |
| Frescura de heartbeat (healthcheck) | ≤ 3 × `HEARTBEAT_INTERVAL_SECONDS` |
| Long polling de Telegram | 25 s |
| Throttle de comandos del bot | 2 s / chat (también callbacks) |
| Expiración de botones inline | 60 s |
| `callback_data` | ≤ 64 bytes |
| Mensaje de Telegram | 4096 caracteres |
| Upload de cookies | 10 MB |
| Throttle de `accounts check` | 30 s |
| Validación periódica de cookies | 6 h, 30–60 s entre sondas |
| Refresco de perfil | 48 h |
| Selfcheck periódico | 24 h |
| Backoff genérico (techo) | 30 min + jitter |
| Backoff anti-bot | 10 s → 120 s |
| Pausa larga del backfill | 60–120 s cada ~50 vídeos |
| Pacing interno de extracción | 1–3 s entre requests |
| Recogida de backfills | 60 s, solo con slot libre |

---

## 12. Estructura de proyecto

```
tikdown-rs/
├── LICENSE                      # MIT
├── .env.example                 # todas las variables de §11, solo valores de ejemplo
├── .gitignore                   # .env, *.db*, /app/data/, videos/, cookies*, .venv/, __pycache__/, .migrate.lock
├── .dockerignore                # T-DEPLOY-11: .env*, data/, videos/, *.db*, cookies*, .git/, .venv/ — re-incluye README.md
├── .python-version
├── README.md
├── pyproject.toml               # [project.scripts] tikdown-rs = "tikdown_rs.cli.main:app"
├── uv.lock
├── alembic.ini                  # copiado explícitamente al runtime Docker (T-DEPLOY-5)
├── Dockerfile                   # multi-stage, patrón oficial de uv
├── docker-compose.yml
├── alembic/
│   ├── env.py                   # template async (T-DEPLOY-2)
│   └── versions/                # copiado explícitamente al runtime (T-DEPLOY-5)
├── src/tikdown_rs/
│   ├── core/                    # config, paths, db, tasks, backoff, errors, verify,
│   │                            # archive, download_engine, pacing, network_monitor,
│   │                            # daemon_state, disk, migrations, cookie_parser,
│   │                            # breaker, logging, notifications/
│   ├── services/                # accounts, cookies, backfill, monitor, videos,
│   │                            # integrity, export, backup, status, static_site
│   ├── cli/                     # main, common, daemon, monitor, accounts, backfill,
│   │                            # cookies, videos, system
│   ├── daemon/                  # run, monitor_job, telegram/{bot,handlers}
│   └── models/
└── tests/
```

**Regla de dependencias**: `services/*` no importa `cli/`, `daemon/` ni `yt_dlp`. El bot no importa
`cli/`. Verificada con un test de arquitectura.

### 12.1 Fases de construcción

| Hito | Contenido | Criterio de aceptación |
|---|---|---|
| **M0 Base** | repo, higiene de secretos, `pyproject` + lock, `Settings` + fail-fast, logging JSON, esqueleto CLI de 7 grupos, Dockerfile/compose, migraciones idempotentes | `docker run --rm <img> tikdown-rs --version` OK; `daemon run` arranca, escribe heartbeat y se apaga limpio con `daemon stop` |
| **M1 Datos y cookies** | modelo completo + migraciones, `cookies add/list/test/remove`, sonda de 3 estados, selfcheck | tests de las 3 categorías; archivo Netscape regenerado cargado con el parser real; `daemon status/healthcheck` |
| **M2 Motor** | `YtDlpEngine` (cookies, formato, clasificación, pacing cross-proceso, integridad) | test de contrato del Protocol; clasificación de literales reales; reserva atómica con dos procesos; **un smoke en vivo mínimo, ejecutado una sola vez a mano** (`@pytest.mark.live`, listar el feed real de una cuenta pública con al menos 1 vídeo) — encarece el riesgo temprano de que el motor nunca haya funcionado contra el objetivo real, en vez de descubrirlo recién en M6. Esta ejecución puntual no entra al gate automático (sigue fuera de él, §13.1) |
| **M3 Backfill** | slot cross-proceso, cursor, `cancel`, `--queue`, `retry-failed`, `--then-monitor` | cancelación sobrevive a un `UPDATE` condicional; transición idempotente con reconciliación en arranque |
| **M4 Daemon completo** | los 7 jobs de §5.3, watcher de `stop_requested` y `monitor_running`, discover → `pending` → download, breaker | cada job con su llamador y su rastro consultable; nacimiento→descarga de un vídeo simulado end-to-end |
| **M5 Bot** | comandos, authz, throttle, paginación, supervisión del polling | authz en comandos y callbacks; recuperación de polling verificada empíricamente |
| **M6 Operación y ronda en vivo** | backups, export, integrity, dashboard estático (§10.3), troubleshooting; ronda contra TikTok real con datos desechables | `system site render` genera los 4 archivos sin recorrer el sistema de archivos; bug encontrado en vivo → test de regresión + fila en el Apéndice A (§16, regla 10) |

M0–M3 son secuenciales. M4 y M5 pueden solaparse. M6 es obligatorio **antes** de considerar el proyecto
estable: los bugs de integración no aparecen con mocks (§13.1, punto 2).

**Gate de cada hito: tests antes que implementación, y el gate es `pytest` en verde, no una lista
marcada como cumplida.** Para cada hito, se escriben primero los tests obligatorios que le correspondan
de §13.2 (en rojo, sin implementación todavía) y recién después el código que los pone en verde. Una
lista en prosa se declara cumplida; un test rojo no admite esa ambigüedad — es la forma más barata de
que una afirmación de "listo" sea verificable en vez de autoreportada.

**Trazabilidad obligatoria en el código: todo archivo nuevo cita, en su docstring de módulo, las trampas
del Apéndice A que neutraliza** (formaliza como criterio de aceptación por hito la práctica de comentarios
que ya pide la regla 6 de §16). No es un adorno: permite auditar, leyendo solo el docstring, si el
archivo entendió **por qué** existe cada pieza de su lógica o si solo imitó la forma de otro archivo
similar sin entender la trampa que la motivó.

---

## 13. Verificación y pruebas

### 13.1 Política

1. **Por defecto, tier determinista**: sin red, sin reloj real, sin disco real fuera de `tmp_path`. SQLite
   `:memory:` + `StaticPool`; `Settings` aisladas por test; cooldown desactivado por defecto en los tests
   del motor (T-ENGINE-8).
2. **Tier en vivo, explícito y separado** [N]: marcado con `@pytest.mark.live`, **fuera** de la suite por
   defecto, requiere cookies y red reales, y nunca corre en un gate automático. Es la única forma honesta
   de cubrir lo que los mocks no cubren: los fallos de integración de esta clase de sistema aparecen en ejecución real, no con mocks. Este tier
   es la única forma honesta de cubrirlos: por eso es explícito y acotado, y nunca corre en un gate
   automático.
3. **Cada bug corregido deja un test de regresión**, siempre que sea reproducible en el tier
   determinista. Si no lo es, se documenta en el Apéndice A con el motivo.
4. **Sin umbral de cobertura numérico** [N]. Un umbral numérico no cubre el riesgo real: se puede tener cobertura alta y, aun así, componentes
   enteros sin llamador. La cobertura se mide, se informa y no bloquea. Lo que bloquea es la lista de
   casos obligatorios de §13.2.
5. Los tests son **F.I.R.S.T.** en el tier determinista: sin `time.sleep` físicos, sin asserts
   dependientes del entorno ni de la hora del día, sin escrituras fuera de `tmp_path` (T-DEPLOY-18, T-DEPLOY-19, T-DEPLOY-21),
   rutas comparadas como `Path` y no como strings con separador fijo (T-DEPLOY-22).
6. Los dobles de test replican la **firma real completa** (`**kwargs`, todos los métodos del Protocol):
   una firma recortada enmascara parámetros muertos y bugs enteros (T-DEPLOY-20, T-ENGINE-16).
7. Los edits multi-bloque se verifican con un import/smoke después de aplicarlos: un edit que no casa
   deja el código inconsistente en silencio (T-ENGINE-9).
8. Gate local obligatorio: `ruff check`, `ruff format --check`, `pytest`. La CI es opcional (§14.4).

### 13.2 Casos obligatorios (cada uno neutraliza una trampa concreta del Apéndice A)

- Cookies: 3 estados, `no entries` → `inconclusive`, `last_validated_at` solo con `valid`/`invalid`;
  `inconclusive` **no** rechaza en `get_working_cookie`; sonda con primera entrada slideshow + segunda con
  vídeo → `valid`; lista de sondas: la segunda candidata sana salva el ciclo; sonda rota → `inconclusive`
  global sin tocar estados.
- Netscape: el tempfile regenerado se carga con el `YoutubeDLCookieJar` **real**.
- Motor: cooldown en `[MIN, MAX]` con RNG inyectable, `MIN=MAX` fijo, `0/0` desactivado; clasificación de
  los literales reales (`requiring login`, `IP address is blocked`, `status code 0`,
  `keeps sending the same page`, `does not have any videos posted`); slideshow → `skipped` sin reintento;
  orden de la cadena de formato (cuál es la primera opción evaluada).
- Listado: `flat_playlist=True` y sin impersonación; entradas `None` filtradas; URL de CDN normalizada a
  URL de página.
- Backfill: cancelación sobrevive al UPDATE condicional y no resucita; `scope_cursor` vs cursor móvil;
  `backfill_total` poblado **después** de listar; interrupción con `downloads_paused=1` → `paused` con
  motivo, sin causa → `queued`, recogida de `paused` solo con la causa resuelta,
  `reconcile_stale_backfills` no toca `paused`; dos adquisiciones concurrentes del slot → solo una gana;
  un vídeo fallido no aborta el lote; `--queue` rechaza `backfilling`.
- Monitor: throttle salta `last_check_at < 30 s` pero **nunca** `NULL`; `backfill_done` cuenta `skipped`;
  `upload_date` ausente usa el cursor anterior actualizado.
- **Modelo/migración**: el `INSERT` de un vídeo descubierto pasa el CHECK real contra una base migrada
  (T-DATA-1); migración sobre el camino real (esquema viejo → rechazo → migrar → OK).
- Daemon: `daemon run` registrado y alcanzable; `daemon stop` detiene el proceso realmente, con un
  segundo proceso escribiendo `stop_requested` (T-CLI-6); `stop_requested` heredado no apaga el
  arranque; imagen Docker con `alembic.ini` accesible en runtime ejecutando un comando que migre (el smoke
  `--version` no cubre esto, T-DEPLOY-5).
- Impersonación: la sonda distingue las 3 causas y **no bloquea el proceso** cuando solo falla la
  inspección; el daemon degradado rechaza `monitor start`/`backfill run` con error accionable.
- Bot: `chat_id` permitido con `from_user.id` no autorizado → rechazado; callbacks throttleados;
  `/check` sin doble `@`; guard tolerante a update sin chat; `clip()` exacto al límite; escape HTML con
  contenido hostil; paginación (clamp, `total_pages`, vacío, escape, ≤64 bytes, expiración 60 s);
  supervisión con `getMe` (y **nunca** `getUpdates`).
- Proceso: test de arquitectura (`services/*` no importa `yt_dlp`/`typer`/SDK del bot); test de humo
  `--help` por comando; test de paridad plantilla↔productor **excluyendo `events.py`** del scan (sin esa
  exclusión la aserción es vacua, T-BOT-13).

---

## 14. Despliegue y operación

### 14.1 Docker (único entorno de producción)

- `DATA_DIR=/app/data`, un solo volumen para base, cookies, archive y vídeos.
- **Multi-stage con el patrón oficial de `uv`**: stage `builder` sobre `python:3.13-slim` con los
  binarios de `uv`, `UV_COMPILE_BYTECODE=1`, `UV_LINK_MODE=copy`, `UV_PYTHON_DOWNLOADS=0`,
  `uv sync --frozen --no-editable --no-install-project` antes de copiar el código (caché de capas).
  **Prohibido** el builder distroless `ghcr.io/astral-sh/uv:latest`: deja un venv con intérprete colgante
  (T-DEPLOY-14).
- Stage `runtime` sobre `python:3.13-slim` con `ffmpeg` instalado explícitamente **y los recursos no
  Python copiados explícitamente** (`alembic.ini`, `alembic/versions/`) — T78. `README.md`
  incluido en el build: hatchling lo exige para el wheel (T-DEPLOY-11).
- Sin comentarios inline dentro de instrucciones `ENV` multilínea: el parser de Docker no los admite
  (T-DEPLOY-12).
- `CMD ["tikdown-rs", "daemon", "run"]`, verificado contra el árbol real de comandos **antes** de fijarlo
  (T-CLI-5).
- `HEALTHCHECK` → `tikdown-rs daemon healthcheck`, con `--start-period` ≥ duración del selfcheck.
- Rotación de logs del contenedor:
  ```yaml
  logging:
    driver: "json-file"
    options: { max-size: "10m", max-file: "5" }
  ```
- Hardening: usuario no root, `tmpfs`, `no-new-privileges`, `--cap-drop=ALL`. Nunca `seccomp=unmasked`
  (incompatible con el intérprete de Python).
- Política de reinicio `on-failure`, **nunca** `unless-stopped`: `daemon stop` termina con salida 0 y
  **debe quedar detenido**, porque lo pidió el operador; `unless-stopped` reinicia ante cualquier salida,
  incluida la limpia, y la comprobación de "se detiene de verdad" de §14.2 parecería fallida aunque
  hubiera funcionado. Un crash (salida distinta de 0) sí se recupera (T-DEPLOY-13).

### 14.2 Primer arranque

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec tikdown-rs test -f /app/alembic.ini    # T-DEPLOY-5
docker compose exec tikdown-rs tikdown-rs daemon selfcheck
docker compose exec tikdown-rs tikdown-rs cookies add /ruta/cookies.txt
docker compose exec tikdown-rs tikdown-rs cookies test 1
docker compose exec tikdown-rs tikdown-rs accounts add @usuario --then-monitor
docker compose exec tikdown-rs tikdown-rs backfill run @usuario --queue
docker compose exec tikdown-rs tikdown-rs monitor start
docker compose exec tikdown-rs tikdown-rs daemon stop       # regresión T-CLI-5: debe detenerse de verdad
```

### 14.3 Hardware y plataforma

Verificar **antes** de comprometerse con ARM64 que existan wheels de `curl-cffi` para la combinación
exacta de versiones y que el selfcheck pase: la disponibilidad de impersonación en `linux/aarch64` fue
históricamente problemática (§B.1.3). Si no hay targets, las opciones documentadas son: imagen `amd64`
por emulación, o hardware `amd64`.

### 14.4 Integración continua

**CI no es un requisito para construir ni para desplegar** (DR-7): el gate local es lo único obligatorio
en todo momento — `ruff check`, `ruff format --check`, `pytest` — y por sí solo cubre, para un solo
desarrollador, el mismo riesgo que cubriría un pipeline remoto.

**Si el operador decide usar CI, la única opción que contempla este plan es Woodpecker CI,
autohospedado — nunca GitHub Actions ni ningún otro CI-as-a-service.** Un único pipeline alcanza:
los mismos tres comandos del gate local más un `docker build` con smoke `--version` — un build exitoso
no implica una imagen que arranca correctamente (§14.2 cubre además el chequeo específico de
`alembic.ini`, que el smoke `--version` por sí solo no detecta, T-DEPLOY-5). La configuración vive en
`.woodpecker/ci-verify.yml`.

**Lo que queda fuera de alcance** (§17.3) es un paso adicional y específico: la publicación automática de
la imagen construida hacia un registro de contenedores. Activar CI y publicar la imagen son decisiones
independientes — la primera es una comodidad operativa que el operador puede o no querer mantener; la
segunda solo tiene sentido si existen consumidores reales de la imagen.

### 14.5 Operación diaria

| Comando | Uso |
|---|---|
| `daemon status` | heartbeat, monitor, selfcheck, tareas, hilos zombis, contención, cookies/disco/errores |
| `daemon healthcheck` | exit 0/1, ligero y sin red |
| `daemon selfcheck` | verificación completa bajo demanda |
| `system disk [--resume]` | espacio, umbral, `downloads_paused` |
| `system backup` | snapshot `VACUUM INTO` en `<DATA_DIR>/backups/` con retención |
| `videos integrity [usuario]` | tamaño + SHA-256 + `ffprobe` |
| `backfill run @user --queue` | encolar o re-encolar |
| `backfill retry-failed @user \| --all` | reintentar fallidos (`--all` con resumen y confirmación) |

**Backup y restauración**: `system backup` conserva los `SYSTEM_BACKUP_RETAIN_COUNT` más recientes.
Restaurar con el daemon detenido: copiar el snapshot sobre `tikdown-rs.db`, borrar `-wal`/`-shm`, volver
a arrancar, `daemon selfcheck`. El backup restaura cuentas, estado y cookies; los vídeos descargados
después del snapshot siguen en disco y el archive evita redescargas duplicadas — revisar
`videos integrity` tras restaurar. **La base contiene las cookies sin cifrar: el backup se trata como
secreto** (§15.2).

### 14.6 Resolución de problemas

**Reglas de diagnóstico**, en este orden:

1. **Un fallo transitorio nunca es un defecto** (§4.4): degradación del extractor, challenges WAF, 403/429 y
   timeouts se clasifican `transient`; no pausan cuentas, no invalidan cookies ni consumen reintentos.
   Antes de depurar, revisar la categoría en `daemon status` → `recent_errors`.
2. **Tras un bump de yt-dlp, el diagnóstico por defecto es el bump** (T-ENGINE-28): `daemon selfcheck` compara
   contra `last_known_good_ytdlp_version`.
3. **Todo bug nuevo que se corrija agrega una fila en las tablas de abajo y, si revela una trampa no
   documentada, una fila en el Apéndice A** (regla 10 del §16).

Cada síntoma remite, entre paréntesis, a la trampa del Apéndice A que lo documenta en detalle — con su
propia clasificación de evidencia `[real]`/`[doc]`/`[raz]`.

#### Primer arranque / despliegue

| Síntoma | Causa | Solución |
|---|---|---|
| Crash-loop, `No such command 'run'` | `daemon run` no registrado contra el árbol real de typer (T-CLI-5) | el `CMD` del Dockerfile y el subcomando se verifican juntos como unidad; re-ejecutar los tests de humo de CLI |
| `FileNotFoundError: alembic.ini` en el primer arranque | el stage runtime no copió los recursos (T-DEPLOY-5) — o los copió con destino relativo ANTES de `WORKDIR /app`, cayendo en `/` aunque `docker history` muestre las capas (T-DEPLOY-23) | `docker compose exec tikdown-rs test -f /app/alembic.ini`; el Dockerfile copia `alembic.ini` + `alembic/` explícitamente y con `WORKDIR /app` antes de todo COPY relativo |
| `daemon stop` reporta éxito pero el proceso sigue vivo | nadie relee `stop_requested` (T-CLI-6) | el watcher sondea cada 0,5 s; buscar `daemon.stop_requested detected via watcher` en logs; un flag heredado no bloquea el arranque siguiente (se limpia al inicio, T-CLI-7) |
| `daemon healthcheck` unhealthy con daemon vivo | heartbeat stale: la frescura debe ser ≤ 3 × `HEARTBEAT_INTERVAL_SECONDS` (T-DEPLOY-7) | revisar carga del sistema; el healthcheck es liviano y nunca migra (T-DEPLOY-1) |
| `docker logs` con 0 bytes y daemon healthy | el `fileConfig()` de Alembic pisó el root logger (T-DEPLOY-6) | corregido: el logging se reaplica con `force=True` inmediatamente después de migrar |
| `IntegrityError: NOT NULL constraint failed: daemon_state.*` en el primer arranque | el `INSERT` nativo del singleton provee solo el `id` (DR-16) | corregido: toda columna NOT NULL de singleton lleva `server_default` en DDL; re-ejecutar migraciones |
| El contenedor sale con código 1 tras `daemon stop` | una excepción sin capturar dentro del camino de apagado | el apagado atrapa los errores de parada del bot, y `KeyboardInterrupt` en Windows ejecuta la limpieza de estado de emergencia. Salida esperada: **0** (`restart: on-failure` depende de ello, T-DEPLOY-13) |

#### Interacciones con TikTok

| Síntoma | Causa | Solución |
|---|---|---|
| `Unable to extract secondary user ID` | degradación intermitente del extractor al resolver la página de perfil — puede ocurrir *con* cookies válidas, y ser intermitente | clasificado transitorio; la cuenta no se pausa y la cookie se conserva. Si persiste 24–48 h, esperar (T-ENGINE-29). Válvula upstream: `YTDLP_EXTRACTOR_ARGS` o URLs `tiktokuser:channel_id` — re-medir antes de hardcodear nada |
| `Unable to extract universal data for rehydration` | rama DASH bloqueada por TikTok (T-ENGINE-10) | el orden por defecto es progresivo primero (`DEFAULT_FORMAT`); override solo vía `DOWNLOAD_FORMAT` y re-medir (DR-4) |
| El listado del feed devuelve 1 entrada | `impersonate` a nivel de parámetros (T-ENGINE-11) | corregido: el motor nunca lo setea; el extractor impersona internamente (DR-4). El propio README de yt-dlp advierte que forzarla degrada velocidad y estabilidad (B.2.10) |
| `File name too long` | URL de CDN sin normalizar a URL de página (T-ENGINE-14) | corregido: toda URL persistida/pasada es `https://www.tiktok.com/@user/video/<id>` |
| `downloads_paused=SÍ` | disco bajo `DISK_WARNING_FREE_PERCENT` (T-ENGINE-27) | liberar espacio → reanudación automática, o `system disk --resume` |
| Rate-limit persistente (challenge WAF, el feed nunca lista) | IP de datacenter / pacing agresivo (T-ENGINE-29) | esperar 24–48 h; nunca asumir que una VPN ayuda. De ser necesario, proxy **residencial** vía `YTDLP_PROXY_URL`, verificado antes de comprometerlo |
| Todo vídeo queda `failed/integrity: no file path resolved` pese a archivos en disco | una nightly de yt-dlp puede mover el path final a `requested_downloads[0]['filepath']` entre versiones | el motor resuelve la ruta por `requested_downloads` + glob por id, nunca por un campo fijo. Si una nightly futura lo cambia de nuevo, correr una descarga de prueba e inspeccionar las claves de `sanitize_info` |
| Un reintento responde "already downloaded" | entrada presente en el archive (T-ENGINE-18) | corregido: `retry-failed` y el fallback degradado descartan la entrada del archive antes de reintentar |

#### Cookies

| Síntoma | Causa | Solución |
|---|---|---|
| `cookies test` → `inconclusive` con cookie válida | perfil-sonda inalcanzable, embedding deshabilitado o primera entrada slideshow (T-COOKIES-2) | la cookie **se conserva** (T-COOKIES-4). Configurar 2–3 perfiles propios verificados en `COOKIE_VALIDATION_URL` (DR-5, separados por comas). Verificar candidatos con `yt-dlp -s <perfil>` antes de desplegarlos |
| `cookie.validation_probe_failed` | todos los candidatos de la sonda fallaron (T-COOKIES-3) | rotar a candidatos sanos; una sonda rota nunca invalida cookies |
| Cookie marcada `invalid` tras una caída de red | imposible por diseño (T-BACKFILL-17/T-COOKIES-3) | si se observa, es un bug: solo un fallo de auth confirmado escribe `invalid` |
| `OverflowError` en el countdown de expiración | valores de epoch absurdos (T-COOKIES-5) | corregido: clamp al año 2100; el countdown es un helper dedicado solo-positivo |

#### Internals del daemon

| Síntoma | Causa | Solución |
|---|---|---|
| Cuenta wedged en `backfilling` tras un crash | estado huérfano (T-BACKFILL-6) | el arranque ejecuta `reconcile_stale_backfills()` (devuelve huérfanos a `queued`, nunca toca `paused`) y libera un `backfill_slot` propiedad de un proceso ya muerto |
| Picos de `database is locked` | contención con escritores paralelos (T-DB-5) | `busy_timeout` se setea **antes** de WAL en cada conexión; monitorear `db_busy_count_5min` en `daemon status` — la alerta solo dispara en el flanco ascendente (§5.6) |
| Una tarea trabada bloquea el apagado | tareas no supervisadas (T-ASYNC-9/T-ASYNC-10/T-ASYNC-11) | corregido: toda tarea de fondo pasa por `create_supervised_task`; el drenaje (10 s) cubre jobs y el worker de backfill |
| Hilos zombis de yt-dlp tras un timeout | `wait_for` no mata el hilo nativo (T-ASYNC-15/T-ASYNC-14) | contados en `daemon status` (`ytdlp_zombie_threads`) y persistidos por el heartbeat; los reintentos escriben a `.retry-N` para que los archivos nunca colisionen |
| `monitor cycle_error: object is not callable` en cada ciclo | pasar el *servicio* de notificaciones donde se espera el callable `emit` | los jobs pasan `notify.emit`, nunca el servicio completo; mantener explícitas las dos formas distintas (`service.emit(event, payload)` vs `on_event(event, payload)`) |
| Variables env tipo lista crashean con `JSONDecodeError` | pydantic-settings decodifica campos complejos como JSON antes de los validadores | `Annotated[list[str], NoDecode, BeforeValidator(split_csv)]` — patrón documentado oficialmente (B.2.6); el test de sincronía `.env.example` (T-DATA-4) cubre la superficie |
| Configuración inválida lanza traceback en la CLI | parseo de pydantic-settings sin capturar (T-CLI-4) | `new_settings()` envuelve errores de parseo/validación en `ConfigError` → `ERROR <mensaje>` + exit 1 |
| Backups/DB legibles por otros usuarios | umask por defecto viola §15.2 regla 2 | corregido: backup con `chmod 0600`; operar el volumen con permisos restringidos |

#### Bot de Telegram

| Síntoma | Causa | Solución |
|---|---|---|
| Bot no responde, `Conflict: terminated by other getUpdates` (409) | otra sesión de `getUpdates` (T-BOT-2) | la verificación es siempre `getMe` (la supervisión reinicia el polling en ≤3 ciclos); nunca llamar `getUpdates` a mano — el test de scan del fuente lo guarda |
| El bot deja de responder sin error visible | polling muerto en silencio (PTB ≥20) | el supervisor con `getMe` reinicia el bot tras `POLLING_HEALTHCHECK_MAX_FAILURES` fallos consecutivos, sin reiniciar el daemon (§6.5) |
| `Message_too_long` / errores de parse entities | T-BOT-6/T-BOT-7 | corregido: `clip()` mantiene el sufijo dentro de 4096; HTML + escape con degradación a texto plano |
| Comandos re-ejecutados tras un reinicio | PTB re-entrega updates no confirmados (T-BOT-4) | por diseño: los handlers son idempotentes (re-agregar una cuenta devuelve error de negocio; re-listar es solo lectura) |
| Un documento de cookies subido es ignorado | tamaño > 10 MB (metadato o real), o chat/usuario no autorizado (§6.3/§7) | usar la vía CLI para cookies sensibles; la vía bot borra el mensaje best-effort tras importar |

#### Desarrollo en Windows

| Síntoma | Causa | Solución |
|---|---|---|
| `PermissionError` al borrar tempfiles | fd quedó abierto (T-COOKIES-7) | `os.close(fd)` inmediatamente después de `mkstemp` — exigido en todo el proyecto |
| `os.kill(pid, 0)` mata el proceso en vez de sondearlo | en Windows mapea a `TerminateProcess` | `services/daemon_control.pid_alive` usa `OpenProcess` en win32 — nunca reemplazarlo por `os.kill` |
| No hay `fcntl` para el lock de migración | solo POSIX | dev corre mono-proceso; el lock degrada a no-op con warning |
| Ctrl+C deja `daemon_pid` stale | sin handlers de SIGTERM en win32 | corregido: `KeyboardInterrupt` dispara `_emergency_cleanup` |

**Estado de los pines** (lo perecedero se reverifica según §2.3; fuente y fecha en el Apéndice B.3):

| Dependencia | Pin | Estado |
|---|---|---|
| yt-dlp nightly | `2026.9.16.232951.dev0` | última nightly verificada al 2026-09-26 |
| curl-cffi | `0.16.0` vía el extra `pin-curl-cffi` | coincide con la nightly; nunca pinear a mano (T-ENGINE-22) |
| SQLAlchemy | `>=2.0.51,<2.1` (2.0.54) | 2.1.x estable desde 2026-08 — se mantiene por decisión (DR-15) |
| APScheduler | `3.11.3` | 4.x sigue en pre-release (re-verificado 2026-09-26, B.2.11) |
| python-telegram-bot | `22.8` | vigente; re-verificar el ciclo manual en un bump mayor (v23) |
| Python | `>=3.13,<3.14` | el techo es una decisión (B.4); revisar en cada bump |
| aiosqlite | `>=0.22.1` | `0.22.0` excluido (hang, B.2.4) |

Retiradas de la configuración (DR-17): `YTDLP_ANTIBOT_BACKOFF_BASE/CEILING_SECONDS` — sin consumidor
mientras no exista el bucle de reintento automático (§17.2); vuelven junto con esa épica.

### 14.7 Preguntas frecuentes

- **¿CLI sin daemon?** Sí, salvo `monitor start/stop` y la ejecución de backfills encolados.
- **¿Cuánto tarda un backfill?** ~40–150 s/vídeo; 1.000 vídeos ≈ 11–42 h; reanudable.
- **¿Y si TikTok cambia el frontend?** El pin a nightly minimiza el impacto; los fallos se clasifican
  como transitorios y **nunca** invalidan cookies por un error del extractor.
- **¿Y si me rate-limitean la IP de forma persistente?** Esperar (24–48 h típico). **Nunca asumir que un
  proxy de datacenter o una VPN comercial ayuda**: medido en un entorno real, una IP de datacenter recibió un WAF challenge que ni siquiera permitía listar
  el feed — el peor caso, no un atajo (T-ENGINE-29). Si persiste, evaluar un proxy **residencial** verificado vía `YTDLP_PROXY_URL`.

---

## 15. Seguridad y secretos

### 15.1 Modelo de amenazas

TikDown-rs **no expone ninguna superficie de red propia**. Accesos: (1) shell/`docker exec` — plano de
confianza raíz; (2) Telegram — solo `TELEGRAM_CHAT_ID` + `from_user.id` autorizado; (3) egreso HTTPS a
TikTok, Bot API y endpoints de probe. Nunca escucha en un puerto.

Activos: cookies de sesión, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `.env` real, vídeos archivados.
**Fuera del modelo**: atacante con shell en el host, acceso físico al volumen, el proveedor del host, o
malware en el contexto del daemon — en todos esos casos ya tiene lectura del `.db` completo, cookies
incluidas.

### 15.2 Sin cifrado en reposo para las cookies (decisión, con su costo medido)

La base contiene `cookie_blob` en Netscape **sin cifrar**. Razonamiento [N]: una clave de cifrado viviría
en el mismo host y el mismo volumen que el secreto que protege, así que no agrega una barrera real ante
ningún atacante del modelo de amenazas; sí agrega superficie de fallo (gestión de clave, pérdida
irrecuperable del secreto, carrera en su generación). La protección real es la misma que para el resto de
la base: control de acceso al host y al volumen.

Costo asumido y medido: el cifrado en reposo arrastra su propia clase de fallos, todos conocidos en
diseños así — carrera en la generación de la clave (el archivo se crea vacío y un lector concurrente lo
lee antes de que se escriba), pin de la clave vía variable de entorno que puede quedar inerte sin que
nadie lo note, y pérdida **irrecuperable** del secreto si la clave se pierde con cookies ya cifradas.

Reglas derivadas (lo que sobrevive a la decisión):

1. La base **nunca** sale del host ni se commitea; los backups se tratan como secretos (§14.5).
2. Todo archivo con secretos en disco tiene permisos restringidos (`0600`) — reformulación de T7.
3. `COOKIE_VALIDATION_URL` sin configurar es configuración incompleta, no un default apuntando a una
   cuenta ajena (§7, DR-5).
4. Ningún secreto en código, tests ni historial: los tests de cookies usan archivos sintéticos.

**Condición de revisión**: si la base o sus backups llegan a salir del host (sincronización a la nube,
NAS compartido, otro operador), esta decisión se revisa — es exactamente el escenario donde el cifrado
en reposo deja de ser cosmético. Se registra entonces en el Apéndice C.

### 15.3 Higiene de publicación

`.gitignore` y `.dockerignore` completos desde el commit inicial (T-DEPLOY-11), `.env.example` con solo valores
de ejemplo, `LICENSE` desde el primer commit, README con qué NO commitear y disclaimer legal estilo
yt-dlp, y **historial limpio antes de cualquier push público** (si algo sensible llegó a commitearse,
reescribir el historial antes del primer push: un secreto en el historial sigue siendo recuperable aunque
no esté en HEAD).

- **Identidad de autoría única y declarada**, en tres lugares que se mantienen en sincronía: los metadatos
  del paquete (`[project] authors`), el `LICENSE` y la configuración de Git del clon. La identidad de Git
  **no viaja con el repositorio**, así que cada clon la fija con `git config user.name "<nombre>"` y
  `git config user.email "<correo>"`; sin eso el historial acumula autores inconsistentes y los cambios
  dejan de ser atribuibles. El valor canónico es una dirección *noreply* del proveedor, pública por
  diseño, nunca un correo real (DR-14).

### 15.4 Telegram

Doble autorización en comandos, callbacks y documentos; throttle 2 s/chat; expiración real de botones;
límite de upload verificado dos veces; verificación **siempre** con `getMe`/`sendMessage` y nunca
`getUpdates`; rotación de token por `@BotFather` + `.env` + reinicio (no requiere migración: el token no
se persiste en la base).

---

## 16. Reglas de trabajo

1. **Una sola capa de lógica de negocio**: `services/*`. `cli/`, `daemon/` y el bot solo orquestan.
2. **DRY real, no dogmático**; **YAGNI** — una abstracción solo con dos usos reales.
3. **Async-first con disciplina**: I/O pesada a `to_thread`; tareas de fondo solo vía
   `create_supervised_task()`; `add_done_callback` síncrono.
4. **Sesiones cortas de base**: nunca abiertas durante I/O de red; los helpers de singleton commitean
   internamente.
5. **Fallos clasificados, nunca excepciones a pelo**: 3 categorías en toda la cadena. Nunca
   `downloaded` sin pasar por `handle_download_result`.
6. **Comentarios de "por qué"**, citando la trampa del Apéndice A cuando aplique (por ejemplo
   `# T-DB-12: session.add no hace ON CONFLICT`).
7. **Nombres precisos y en inglés** en código y commits.
8. **Todo `Protocol` con implementación real tiene test de contrato** (T-ENGINE-16).
9. **Todo artefacto de despliegue se verifica ejecutando de verdad los caminos que dependen de él**
   (Dockerfile → recursos no Python, T-DEPLOY-5; CLI → comandos registrados, T-CLI-5).
10. **Registrar cada bug encontrado en ejecución real** en el mismo turno: causa raíz, archivos, si dejó
    test de regresión, **fila nueva en la tabla de síntomas de §14.6** y **fila nueva en el Apéndice A**
    si revela una trampa no documentada. Un bug observado en ejecución real pesa más que cualquier
    suposición derivada de leer documentación.
11. **Un fallo transitorio o de red nunca se registra como defecto de código** ni invalida cookies ni
    consume reintentos.
12. **Ante duda de diseño**: manda este plan; si no la resuelve, se decide con §0.2 y se registra en el
    Apéndice C.

---

## 17. Fuera del alcance base (con criterio de activación)

Nada de esta sección se implementa en el alcance base. Cada punto declara **qué lo activaría** y **qué
cuesta**, para que la decisión sea del usuario y no del momentum del implementador.

### 17.1 Envío push de notificaciones [F]

**Qué era**: `ExtBot` real + spool persistente `pending_notifications` + coalescing de ráfagas + catálogo
de ~25 eventos con test de paridad plantilla↔productor + variables `ENABLE_EXTERNAL_NOTIFICATIONS` y
`TELEGRAM_BOT_MODE`.

**Por qué sale**: un canal push declarado como base sin ciclos emisores cableados no tiene nada que
notificar: primero tienen que existir los jobs (§5.3), y eso ya es el alcance base. Declararlo antes
produce las cuatro señales de una capacidad a medias —servicio noop, tabla de spool sin lecturas ni
escrituras, eventos sin productor y variables de entorno sin efecto—, que es peor que no declararlo.

**Qué lo activaría**: querer enterarse de fallos, cookies inválidas o caídas de red sin mirar el daemon.
Lo primero a implementar es el **spool**, no el envío: persistir el evento original (nunca el texto
renderizado), drenarlo al volver online y en el arranque, y recién después el envío real.

**Lo que ya queda listo en el alcance base** (§6.2): el catálogo de plantillas, la función de render, el
canal síncrono inyectado y el `NoopNotificationService` por defecto. Con eso, activar el envío es cablear
un servicio, no rediseñar.

**Restricciones que se mantienen cuando se implemente**: captura amplia de excepciones en el envío (un
fallo de red no puede reventar el flujo que lo originó, T-BOT-16); coalescing con condición `>=` umbral y
bandera consumible, nunca `==` (T-BOT-17); el test de paridad **excluye `events.py`** del scan de literales
(sin eso la aserción es vacua, T-BOT-13); el render **no** agrega `@` (las plantillas ya lo traen, T-BOT-10).

### 17.2 Techo de reintentos y presupuesto de tiempo por vídeo [F]

**Qué sería**: un contador de reintentos por vídeo (`MAX_VIDEO_RETRY_COUNT`) y un presupuesto de tiempo
total acumulado (`MAX_VIDEO_TOTAL_TIME_SECONDS`) que acoten un bucle de reintento automático — **ninguna
de las dos variables existe en §11.1** mientras esta capacidad no se active (C-4).

**Por qué sale**: sin un bucle de reintento automático no hay nada que acotar. Un contador, un techo y un
presupuesto de tiempo declarados antes de que exista el bucle que los consume son exactamente el patrón
que describe la regla de cableado (§0.3): lógica completa, testeable en aislamiento, y sin ningún
llamador real en el camino de producción — el modo de fallo más caro de esta arquitectura, porque los
tests en verde no lo detectan.

**Qué lo activaría**: introducir reintento automático (por ejemplo, reintentar fallidos transitorios al
final de cada ciclo del monitor). En ese momento se implementan juntos: contador, techo, presupuesto y
las dos variables de entorno, con la regla de que **los fallos de red no consumen ninguno de los dos**.

### 17.3 Publicación automática de imágenes multi-arquitectura [F]

**Por qué sale**: la CI (§14.4) ya no es un requisito para construir ni desplegar — el gate local
(`ruff` + `pytest` + `ruff format --check`) cubre el mismo riesgo para un solo desarrollador sin exigir
ninguna infraestructura de CI. Lo que queda estrictamente fuera de alcance es un paso adicional y
específico: la **publicación automática** de la imagen construida hacia un registro de contenedores, que
solo tiene sentido si existen consumidores reales de esa imagen además del propio operador.

**Qué lo activaría**: que exista un segundo desarrollador o un consumidor externo de la imagen. En ese
momento, el pipeline de Woodpecker que ya construye y hace smoke de la imagen (§14.4) se extiende con un
paso de publicación etiquetada por tag/commit.

### 17.4 Otros diferidos

- Slot de backfill como barrera a nivel de **red** distribuida (hoy es cross-proceso a nivel de host,
  suficiente para un solo host).
- Rotación/clasificación de IP de salida con pools de proxies, y auto-descubrimiento de listas de
  proxies (mantener apagado por defecto).
- Comando de reparación de rutas para migrar el volumen a otro host (`local_path` es absoluto).
- Persistencia del offset de `getUpdates` en SQLite (hoy en memoria de PTB, T-BOT-4).
- Archivado de slideshows como audio + carátula (hoy `skipped`).
- Tipado más estricto en contratos donde hoy hay `Any`.

---

## Apéndice A — Trampas verificadas

**Cómo leer.** Cada fila es un modo de fallo concreto, no una hipótesis teórica. La columna *Regla* es la
norma que lo neutraliza (el detalle está en la sección indicada) y la columna *Origen* clasifica la
naturaleza de la evidencia:

- **[real]** — fallo ya observado ejecutando el sistema contra el entorno real. Es el tipo de fila que
  **no** se discute: existe porque algo se rompió de verdad.
- **[doc]** — restricción derivada de documentación oficial de una dependencia (fuente y fecha en el
  Apéndice B).
- **[raz]** — razonamiento de diseño sin incidente: la fila **no** debe tratarse como ley, pero tampoco
  se borra sin motivo (basta un test barato para cubrirla).

Los identificadores siguen un **único esquema**, `T-<ÁREA>-<N>` (§0, arriba): un solo formato para
las 9 subsecciones de este apéndice, secuencial dentro de cada área, sin excepciones ni variantes.
Son etiquetas **internas de este documento**, estables para citarlas desde comentarios de código y
desde las secciones del núcleo normativo — una trampa nueva se agrega al final de la numeración de su
área (nunca se reordena ni se reutiliza un número retirado). Las clases de fallo que este diseño evita
por decisión propia están en A.10.

### A.1 CLI, typer y salida de consola

| # | Qué falla | Regla | Origen |
|---|---|---|---|
| T-CLI-1 | `UnicodeEncodeError` con glifos no-ASCII en consola Windows legacy (cp1252, salida con pipe) | marcadores ASCII puros en toda la salida CLI | §10.2 [real] |
| T-CLI-2 | `tikdown-rs --help` → `RuntimeError: Could not get a command for this Typer instance` | `@app.callback()` global con `--version` + `invoke_without_command=True` | §10.2 [real] |
| T-CLI-3 | Rich envuelve a 80 columnas en no-TTY y corrompe exports largos; markup en celdas crudas revienta | export con `markup=False, soft_wrap=True`; celdas dinámicas como `rich.text.Text()` | §10.2 [real] |
| T-CLI-4 | Errores de negocio salen como traceback con stdout vacío | `run_or_exit()` → `ERROR <mensaje>` + exit 1 | §10.2 [real] |
| T-CLI-5 | `daemon run` sin registrar: crash-loop del contenedor con `No such command 'run'` desde el primer arranque | todo comando de §10.1 registrado y verificado con test de humo `--help`; `CMD` y subcomando se verifican juntos | §10.1 [real] |
| T-CLI-6 | `daemon stop` escribe la bandera pero nadie la relee: el daemon queda zombi pese a que la CLI reporta éxito | polling activo de `stop_requested` en el loop, no solo señales | §5.3 [real] |
| T-CLI-7 | `stop_requested` heredado de una sesión anterior autoapaga el arranque siguiente | `_clear_stop_requested()` en `_start()` | §5.1 [real] |
| T-CLI-8 | `system backup` sin mapear `sqlite3.OperationalError` (disco lleno o base bloqueada durante `VACUUM INTO`) revienta con traceback crudo en vez de `ERROR <mensaje>` + exit 1 | todo comando de la CLI mapea las excepciones de `services/*` a la convención de error de §10.2, `system backup` incluido | §10.2 [real] |
| T-CLI-9 | `rich.progress`: un campo propio leído como `{task.fields.clave}` lanza `AttributeError` en el primer render, y `total`/`completed` están reservados y mutan la barra real | `{task.fields[clave]}` (corchetes) + nombres propios no colisionantes (`procesados`, `correctos`, `fallidos`) + test de render con datos simulados completos | §10.2 [real] |
| T-CLI-10 | `videos export` con títulos reales de TikTok (emoji/unicode) revienta en consola Windows legacy: `typer.echo` codifica con el codepage de la consola (cp1252) → `UnicodeEncodeError` + traceback completo (ronda en vivo M6; T-CLI-1 protege la UI, pero el export ES dato) | el payload de export se escribe como bytes UTF-8 explícitos en `sys.stdout.buffer` (la redirección produce archivos UTF-8 válidos); fallback a echo cuando stdout no tiene buffer | §10.2 [real] |
| T-CLI-10 | Un comando `async def` en la CLI nunca se ejecuta: typer no tiene soporte async nativo y la corrutina queda sin esperar | wrappers síncronos con `asyncio.run(...)` centralizados en `cli/common.py`, nunca dentro de un loop ya activo | §10.2 [doc] |

### A.2 asyncio, tareas y ciclo de vida

| # | Qué falla | Regla | Origen |
|---|---|---|---|
| T-ASYNC-1 | `add_done_callback` con función async nunca se ejecuta | callback **síncrono** que audita con `task.exception()` | §5.5 [real] |
| T-ASYNC-2 | `TypeError: Logger._log() got an unexpected keyword argument 'name'` en el callback de auditoría | interpolar el nombre en el mensaje, no pasar `name=` al logger | §5.5 [real] |
| T-ASYNC-3 | El daemon no se apaga: un `asyncio.run()` por fase deja el scheduler en un loop muerto y el watcher sin disparar | **un único** `asyncio.run(_lifecycle())` | §5.1 [real] |
| T-ASYNC-4 | `RuntimeError: cannot be called from a running event loop` al migrar en el arranque | migraciones en `to_thread` | §5.1 [real] |
| T-ASYNC-5 | `asyncio.run()` dentro de un test ya async | helpers `async` con `await` | §13.1 [real] |
| T-ASYNC-6 | El evento `daemon.stopped` crea una tarea de notificación después del drenaje y casi nunca se entrega | con servicio Noop no se crea tarea; el evento se emite **antes** del drenaje | §5.2 [real] |
| T-ASYNC-7 | `wait_for(lock.acquire(), timeout=0)` siempre lanza `TimeoutError` | `if lock.locked(): return False; await lock.acquire()` | §4.5 [real] |
| T-ASYNC-8 | `ffprobe`/SHA-256/`fsync` bloquean el event loop | `asyncio.to_thread` para toda I/O pesada | §1.1 [real] |
| T-ASYNC-9 | `AsyncIOScheduler.shutdown(wait=True)` no espera los jobs en curso, los cancela | drenaje real por el registro de tareas supervisadas | §5.2 [doc] |
| T-ASYNC-10 | Jobs del scheduler fuera del drenaje del apagado | jobs de ciclo largo como tareas supervisadas | §5.3 [real] |
| T-ASYNC-11 | El registro guarda referencias sin `Task` real: no se pueden cancelar | guardar `_task_refs` reales | §5.2 [real] |
| T-ASYNC-12 | Índice de tareas por nombre → colisión | indexar por `id(task)` | §5.5 [real] |
| T-ASYNC-13 | Un job más lento que su intervalo se solapa | `max_instances=1` + `coalesce=True` | §5.3 [real] |
| T-ASYNC-14 | El hilo zombi de yt-dlp sigue escribiendo el mismo `outtmpl` que el reintento | reintentos a `.retry-N`, renombrar solo tras integridad | §4.5 [real] |
| T-ASYNC-15 | `wait_for` con timeout no mata el hilo nativo de yt-dlp | documentar; confiar en la verificación de integridad + `.retry-N` | §4.5 [real] |
| T-ASYNC-16 | Un job cachea una bandera de `daemon_state` al registrarse y nunca ve el cambio | releer la bandera en cada ejecución | §5.3 [real] |

### A.3 SQLite y SQLAlchemy

| # | Qué falla | Regla | Origen |
|---|---|---|---|
| T-DB-1 | `MissingGreenlet` al leer una relación lazy fuera de la sesión | cargar con `selectinload(...)` | §3.7 [real] |
| T-DB-2 | `DetachedInstanceError` al acceder a `video.account` desde el CLI | `selectinload` en las consultas compartidas CLI/bot | §3.7 [real] |
| T-DB-3 | `AttributeError: 'async_sessionmaker' object has no attribute 'execute'` | abrir la sesión, no ejecutar sobre el sessionmaker | §3.7 [real] |
| T-DB-4 | Mutaciones que no persisten: el objeto quedó detached de otra sesión | consultar dentro de la sesión activa | §3.7 [real] |
| T-DB-5 | `database is locked` en el `PRAGMA journal_mode=WAL` con dos procesos arrancando | `busy_timeout` **antes** de `journal_mode` | §3.7 [real] |
| T-DB-6 | El cooldown cross-proceso falla **hacia fail-open** porque el singleton nunca se commitea | commit inmediato del `INSERT ... ON CONFLICT DO NOTHING` | §3.5 [real] |
| T-DB-7 | Dos reservas rápidas colapsan al mismo timestamp y el CAS pierde la distinción | `timespec="milliseconds"` | §3.5 [real] |
| T-DB-8 | Líneas duplicadas en el archive: yt-dlp escribe `tiktok <id>`, el código `<id>` | el ID es el **último token** de la línea | §3.6 [real] |
| T-DB-9 | `IndexError` al crear un engine sobre URL de memoria | derivar "es en memoria" de la URL **parseada** (base de datos vacía o `:memory:`), nunca buscando `///` en el texto: `sqlite+aiosqlite:///:memory:` lleva `///` y es en memoria | §3.7 [real] |
| T-DB-10 | `stamp("head")` sobre una base que ya tiene la tabla marcadora pero es anterior a una migración posterior: queda marcada en `head` sin haber recibido esa migración, y el síntoma aparece mucho después, en la primera escritura que dependa de lo que faltaba | `stamp` apunta siempre a `MARKER_TABLE_REVISION` (la revisión exacta que creó la tabla marcadora), nunca a `head`, seguido siempre de `upgrade("head")` en el mismo paso — así ninguna migración posterior queda sin aplicar (DR-12) | §5.4 [real] |
| T-DB-11 | Singleton sin `ON CONFLICT` → carrera en el primer arranque | `INSERT ... ON CONFLICT DO NOTHING` + relectura | §3.5 [real] |
| T-DB-12 | `session.add()` + commit sobre la fila singleton ya existente → `IntegrityError` y **0 descargas siempre** | `INSERT ... ON CONFLICT DO NOTHING` nativo | §3.5 [real] |
| T-DB-13 | Mutación de `daemon_state` sin commit → rollback silencioso | helpers mutadores que commitean internamente | §3.7 [real] |
| T-DB-14 | El contador de contención leído desde el proceso CLI siempre da 0 | listener + persistencia en heartbeat + lectura desde `daemon_state` | §5.6 [real] |
| T-DB-15 | Sesión SQLite abierta durante una llamada de red | leer blob → cerrar sesión → validar → reabrir | §7 [real] |

### A.4 Motor, listado de feeds y formato

| # | Qué falla | Regla | Origen |
|---|---|---|---|
| T-ENGINE-1 | 403 genérico tratado como definitivo → pausa cuentas sanas | 403 sin hints de auth = transitorio; orden de evaluación estricto | §4.4 [real] |
| T-ENGINE-2 | Marcadores de auth literales que no casan con los mensajes reales del extractor | lista derivada de literales reales | §4.4 [real] |
| T-ENGINE-3 | `status code 0` tratado como contenido inexistente | transitorio | §4.4 [real] |
| T-ENGINE-4 | `keeps sending the same page` tratado como fallo de cuenta | transitorio | §4.4 [real] |
| T-ENGINE-5 | Slideshow clasificado como fallo de integridad | `expected_has_video: bool` → `skipped` sin reintentos | §4.7 [real] |
| T-ENGINE-6 | `AssertionError` en toda descarga real: los targets se construían como **strings** y el parámetro exige `ImpersonateTarget` | conservar los **objetos** de la API de targets | §4.1 [real] |
| T-ENGINE-7 | `engine.download()` colgado indefinidamente: el `asyncio.Event` de red se crea sin setear | el evento por defecto se crea **seteado** | §8.1 [real] |
| T-ENGINE-8 | Los tests del motor tardan 30–120 s por descarga por el cooldown por defecto | cooldown desactivado por defecto en tests | §13.1 [real] |
| T-ENGINE-9 | `AttributeError` por un edit multi-bloque que aplicó solo una mitad | verificar con import/smoke tras aplicar edits multi-bloque | §13.1 [real] |
| T-ENGINE-10 | El formato DASH exige resolver el vídeo completo y TikTok lo bloquea | rama progresiva primero; orden medido y configurable | §4.2 [real] |
| T-ENGINE-11 | Impersonar en el **listado** reduce la extracción a 1 entrada (algunos targets fallan y `ignoreerrors` los descarta) | el listado no usa `impersonate` a nivel de parámetros | §4.6 [real] |
| T-ENGINE-12 | Listar sin `flat_playlist=True` resuelve cada entrada y TikTok bloquea intermitentemente | `flat_playlist=True` siempre | §4.6 [real] |
| T-ENGINE-13 | `flat_playlist` + `ignoreerrors` deja entradas `None` → `AttributeError` | filtrar `entry is not None` | §4.6 [real] |
| T-ENGINE-14 | El feed con cookies devuelve URLs de CDN con tokens gigantes → `File name too long` | normalizar a URL de página canónica | §4.6 [real] |
| T-ENGINE-15 | Los targets de impersonación interfieren con el listado: menos entradas que sin usarlos | ver T-ENGINE-11; medir antes de cambiar el default | §4.6 [real] |
| T-ENGINE-16 | `Protocol` declara métodos que la clase concreta no implementa → `AttributeError` en cada backfill | test de contrato sobre la clase concreta | §4.8 [real] |
| T-ENGINE-17 | El job del daemon pasa el engine de SQLAlchemy como motor de descarga | construir el motor real; nombres `db_engine`/`download_engine` | §4.8 [real] |
| T-ENGINE-18 | Reintentar con formato mejorado sin descartar antes la entrada del archive | descarte **antes** del segundo intento | §4.7 [real] |
| T-ENGINE-19 | `accounts check` con motor y clave simulados reporta éxito sin hacer nada | motor y cookies reales | §10.1 [real] |
| T-ENGINE-20 | El nightly se normaliza en PyPI (PEP 440) y no coincide con el tag de GitHub | comparar siempre contra `yt_dlp.version.__version__` | §2.1 [doc] |
| T-ENGINE-21 | `prerelease="allow"` global mete alphas de otros paquetes (incluso transitivas) al lock | `prerelease-package` acotado a yt-dlp | §2.1 [doc] |
| T-ENGINE-22 | Serie de `curl-cffi` incompatible → todos los targets `(unavailable)` y 403 silenciosos | pin exacto vía el extra `pin-curl-cffi`; sonda que distingue 3 causas | §4.1 [doc] |
| T-ENGINE-23 | Enumeración de feeds sin pacing interno | `sleep_interval_requests` + `extractor_retries` | §4.2 [real] |
| T-ENGINE-24 | Un nombre de archivo que empieza con `-` se lee como opción de `ffprobe` | `--` antes de la ruta | §4.7 [real] |
| T-ENGINE-25 | `upload_date` con formatos mezclados rompe el cursor | formato único canónico `YYYYMMDD` | §3.2 [real] |
| T-ENGINE-26 | Un cooldown fijo entre descargas produce un patrón temporal fingerprinteable | sorteo uniforme en `[MIN, MAX]` con RNG inyectable; `MIN=MAX` es el caso fijo y `0/0` el desactivado | §4.5 [real] |
| T-ENGINE-27 | Disco lleno (ENOSPC) clasificado como error genérico de red o de cuenta | rama propia: `downloads_paused=1` + aviso, sin contar para el breaker ni tocar cookies | §8.2 [real] |
| T-ENGINE-28 | Tras actualizar yt-dlp la extracción se rompe por una regresión de la nueva versión, y el diagnóstico apunta a TikTok | comparar la versión en uso contra `last_known_good_ytdlp_version`: si el selfcheck falla con una versión distinta de la última buena, la causa por defecto es la actualización | §2.1 [real] |
| T-ENGINE-29 | Cambiar a una IP de datacenter o VPN comercial para "evitar" un rate-limit empeora la situación (WAF challenge que ni permite listar el feed) | ante un rate-limit persistente, esperar (24-48 h) o usar un proxy **residencial** verificado; nunca asumir que cualquier cambio de IP mejora | §14.7 [real] |
| T-ENGINE-30 | El challenge WAF de TikTok se resuelve con un PoW de SHA-256 en **Python nativo** (hasta 10^6 iteraciones, cookie `_wafchallengeid`); si el extractor no lo resuelve, lanza `Unable to solve JS challenge` / `Unable to extract challenge data` | esos fallos son **transitorios** (una cuenta sana no se pausa por un challenge no resuelto); el solver es CPU-bound y corre en `to_thread` | §4.4 [doc] |
| T-ENGINE-31 | `accounts add` acepta la URL del perfil y la persiste verbatim; `list_videos`/`extract_profile` hacen `lstrip("@")` y arman `https://www.tiktok.com/@https://www.tiktok.com/@user` — TikTok redirige a `/foryou?lang=en` y con `ignoreerrors` el listado vuelve VACÍO y silencioso (backfill `completed total=0` en una cuenta con vídeos; hallado en la ronda en vivo M6) | normalizar el handle en el MOTOR (`normalize_handle`: acepta URL/`@user`/`user`, rechaza no parseable con `ValueError`) — un guard cubre los 3 consumidores (backfill, monitor, refresh de perfil) | §4.6 [real] |
| T-ENGINE-32 | yt-dlp SALTA el download cuando el archivo final ya existe en disco (`has already been downloaded`) y devuelve `requested_downloads` vacío; el motor lo trataba como fallo de integridad — `retry-failed` de filas cuyo archivo sobrevivió no convergía jamás (ronda en vivo M6: 17 archivos en disco, 17 filas churneando `failed/integrity`) | adoptar el archivo por glob-by-id en `_resolve_downloaded_path` (nunca `.part`); sin archivo en disco, el fallo se surfaced como siempre | §4.7 [real] |
| T-ENGINE-33 | El argv de ffprobe tenía DOS trampas reales: (1) `--` inmediatamente tras `v:0` dejaba `-show_entries`/`-of` DESPUÉS del separador → ffprobe los parseaba como archivos de entrada y salía 1 → `{}`; (2) las secciones de `-show_entries` se separaban con `,` en vez de `:` → la sección `streams` nunca se emitía. Resultado: `has_video` SIEMPRE False — con ffprobe instalado, TODO download degradaba a `failed/integrity` (ronda en vivo M6: 17 descargas al 100%, 17 filas falladas). Invisible a los tests: el ffprobe estaba stubbeado (punto ciego del mock) | argv construido por `_ffprobe_command` puro: opciones antes de `--`, archivo al final, secciones con `:` (`format=duration:stream=codec_name,width,height`); test de construcción + test de integración real skipif sin ffprobe | §4.7 [real] |

### A.5 Cookies

| # | Qué falla | Regla | Origen |
|---|---|---|---|
| T-COOKIES-1 | El parser real de CPython exige el magic header Netscape; un parser propio tolerante lo enmascara | header obligatorio + `newline="\n"`; tests con `YoutubeDLCookieJar` real | §4.3 [real] |
| T-COOKIES-2 | Sonda con embedding deshabilitado o primera entrada slideshow → `inconclusive` con cookie válida | iterar 5 entradas; verificar la sonda con extracción real antes de desplegar | §7 [real] |
| T-COOKIES-3 | Una sonda rota invalida todas las cookies | sonda rota ⇒ `inconclusive` global sin tocar estados | §7 [real] |
| T-COOKIES-4 | `get_working_cookie` rechaza una cookie válida porque el estado era `inconclusive` | solo rechaza ante `invalid` | §7 [real] |
| T-COOKIES-5 | Fecha de expiración absurda → `OverflowError` | clamp al año 2100 | §7 [real] |
| T-COOKIES-6 | Tempfile huérfano si la escritura falla a medias | `mkstemp` + `os.close(fd)` inmediato + limpieza en `finally` | §4.3 [real] |
| T-COOKIES-7 | En Windows el tempfile no se puede borrar: el fd quedó abierto | `os.close(fd)` inmediatamente tras `mkstemp` | §4.3 [real] |
| T-COOKIES-8 | Un fallo de limpieza posterior a un éxito confirmado aborta el procesamiento | best-effort con warning | §7 [real] |

### A.6 Backfill, monitor y cuentas

| # | Qué falla | Regla | Origen |
|---|---|---|---|
| T-BACKFILL-1 | El throttle salta cuentas **nunca** comprobadas (`NULL` tratado como 0 s) | una cuenta sin `last_check_at` se comprueba siempre | §4.6 [real] |
| T-BACKFILL-2 | El backfill para tras el primer vídeo: el `break` usa el cursor móvil | `scope_cursor` separado del móvil | §9.2 [real] |
| T-BACKFILL-3 | `upload_date` ausente recibe el cursor **inicial** en vez del anterior | actualizar el cursor tras cada vídeo | §4.6 [real] |
| T-BACKFILL-4 | `backfill_total` nunca se persiste: progreso siempre `N/0` | persistir el total al iniciar cada pasada | §9.3 [real] |
| T-BACKFILL-5 | El total se calcula sobre la variable que todavía es `None` → queda en 0 | calcularlo **después** de listar el feed | §9.3 [real] |
| T-BACKFILL-6 | Backfills wedged en `backfilling` tras un crash, bloqueando reintentos | listado dentro del `try`; `CancelledError` → `queued`/`paused`; reconciliación en el arranque | §9.1 [real] |
| T-BACKFILL-7 | La persistencia de progreso pisa un `cancel` concurrente | UPDATE condicional con `WHERE backfill_status='backfilling'` | §9.4 [real] |
| T-BACKFILL-8 | La cancelación se pisa con `completed` y dispara la transición | retorno temprano sin transición ni evento de completado | §9.4 [real] |
| T-BACKFILL-9 | `backfill cancel` → `IntegrityError`: `'cancelled'` no estaba en el CHECK | todos los estados del enum en el CHECK desde la migración inicial | §3.1 [real] |
| T-BACKFILL-10 | `backfill cancel` solo escribe en la base y no detiene el proceso | relectura periódica + UPDATE condicional + retorno temprano | §9.4 [real] |
| T-BACKFILL-11 | Un vídeo fallido aborta **todo** el backfill de la cuenta | `try/except` por vídeo + continuar | §9.6 [real] |
| T-BACKFILL-12 | La CLI pasaba `cookies=[]` hardcodeado: todo backfill abortaba con `no_cookies` | cargar las cookies reales en cada punto de entrada | §9.6 [real] |
| T-BACKFILL-13 | El canal de eventos no se propaga a la corrutina lanzada por un job | propagar `on_event` explícitamente | §4.7 [real] |
| T-BACKFILL-14 | `download.completed` nunca llega desde el backfill: falta propagar `notify_on_download` | propagarlo en **todas** las rutas | §4.7 [real] |
| T-BACKFILL-15 | El canal de eventos se envuelve en `async def` y la corrutina nunca se ejecuta | el canal es **síncrono** | §4.7 [real] |
| T-BACKFILL-16 | La transición history→monitor se escribe por separado del completado | misma transacción + reconciliación defensiva en el arranque | §9.5 [real] |
| T-BACKFILL-17 | Una caída de red se trata como fallo de la cuenta o de la cookie: marca `invalid`, incrementa `retry_count` y pausa cuentas sanas | un fallo de red no produce `validation_state='invalid'`, no consume reintentos y no cuenta para el breaker | §8.1 [real] |
| T-BACKFILL-18 | `--then-monitor` deja la cuenta en `monitor` pero el monitor global detenido | la transición también activa `monitor_running` | §9.5 [real] |
| T-BACKFILL-19 | No existe forma de re-encolar un backfill terminado sin resetear la base a mano | `--queue` re-encola `completed`/`failed` y rechaza `backfilling` | §9.6 [real] |
| T-BACKFILL-20 | Coordinación por instancia/proceso: sin coordinación real cross-proceso | slot y reloj persistidos en SQLite con `RETURNING` atómico | §3.5/§9.1 [real] |
| T-BACKFILL-21 | Rutas relativas de vídeos → fuera del volumen en Docker | todo deriva de `DATA_DIR` vía `core/paths.py` | §4.5 [real] |

### A.7 Bot de Telegram y notificaciones

| # | Qué falla | Regla | Origen |
|---|---|---|---|
| T-BOT-1 | `run_polling()` dentro de un loop existente → `RuntimeError` | `initialize() → start() → updater.start_polling()` | §6.1 [doc] |
| T-BOT-2 | Un `getUpdates` manual mata el polling del bot en silencio (409 Conflict) | nunca `getUpdates` manual; verificar con `getMe`; supervisión activa | §6.1/§6.5 [real] |
| T-BOT-3 | El bot crea un engine nuevo por comando | dependencias inyectadas + `owns_engine` | §6.1 [real] |
| T-BOT-4 | Re-entrega de updates tras un reinicio | handlers idempotentes | §6.1 [doc] |
| T-BOT-5 | `callback_data` limitado a 64 bytes | encoding compacto presupuestado | §6.4 [doc] |
| T-BOT-6 | Mensaje >4096 caracteres → `Message_too_long` | `clip()` compartido con el sufijo dentro del límite | §6.3 [doc] |
| T-BOT-7 | MarkdownV2 + contenido de TikTok → `can't parse entities` | `parse_mode=HTML` + `html.escape()`; degradación a texto plano | §6.3 [doc] |
| T-BOT-8 | Ráfaga de notificaciones → 429 | `AIORateLimiter(max_retries=3)`; el extra `[rate-limiter]` es obligatorio | §6.3 [doc] |
| T-BOT-9 | `/check @user` responde `@@user` | `lstrip('@')` en toda interpolación | §6.3 [real] |
| T-BOT-10 | Todas las plantillas con `@@user` | el render no agrega `@` | §6.3 [real] |
| T-BOT-11 | Guard que revienta con updates sin `effective_chat` | guard tolerante + throttle en callbacks | §6.3 [real] |
| T-BOT-12 | Notificación de descarga sin contexto accionable | URL + categoría + motivo + siguiente paso | §17.1 [real] |
| T-BOT-13 | Evento sin plantilla → pérdida silenciosa (y test de paridad vacuo si incluye el catálogo) | catálogo + test de paridad **excluyendo `events.py`** | §13.2 [real] |
| T-BOT-14 | `network.online` notificado tras un blip de duración 0 | capturar la duración antes de limpiar; notificar solo desde offline confirmado | §8.1 [real] |
| T-BOT-15 | Eventos emitidos durante una caída de red se pierden | spool persistente (diferido a §17.1; el contrato ya está en §6.2) | §17.1 [real] |
| T-BOT-16 | Un fallo de red no-`TelegramError` revienta el envío y bloquea el flujo de origen | captura amplia con spool cuando esté habilitado | §17.1 [real] |
| T-BOT-17 | El resumen de coalescing solo se emite en el instante exacto del umbral | `>=` umbral + bandera consumible | §17.1 [real] |
| T-BOT-18 | Un guard de autorización que asume `update.effective_chat` siempre presente revienta con `AttributeError` ante un update sin chat (por ejemplo, ciertos `channel_post` o `poll_answer`) | todo guard de autorización tolera `effective_chat=None` explícitamente antes de comparar el id | §6.3 [real] |

### A.8 Modelo, descubrimiento y datos

| # | Qué falla | Regla | Origen |
|---|---|---|---|
| T-DATA-1 | `daemon_discover` inserta un `status` fuera del CHECK; el commit falla, el error queda como `WARNING` y el resultado es indistinguible de "no hay nada nuevo" | el estado `pending` está en el CHECK y el test de migración ejerce el camino real contra una base migrada | §3.3 [real] |
| T-DATA-2 | El ciclo del monitor, el chequeo de disco, el probe de red y el breaker pueden quedar completos, testeados en aislamiento, y **sin ningún llamador** real en el camino de producción | cada capacidad se implementa junto con su llamador y su propio test de humo end-to-end (§0.3) | §5.3 [real] |
| T-DATA-3 | Clasificar un vídeo pendiente con una heurística propia y débil (por ejemplo, marcar todo como `transient` sin distinguir causa) en vez de reutilizar el clasificador y el verificador ya existentes | usar siempre `ffprobe` para verificar y el clasificador de errores único del proyecto (§4.4) para categorizar | §4.7 [real] |
| T-DATA-4 | `.env.example` desincronizado (7 variables muertas, 19 campos sin documentar) | cada variable con efecto real o retirada; test de sincronía contra `Settings` | §11.1 [real] |
| T-DATA-5 | Un typo en una variable de entorno se traga en silencio y el usuario cree tener una configuración activa | aviso por variable desconocida, derivado de los prefijos reales de `Settings` | §11.1 [real] |
| T-DATA-6 | Un filtro mal escrito en `videos integrity <username>` deja pasar vídeos de **cualquier** cuenta, no solo la nombrada | filtrar siempre por join real con `monitored_accounts`, nunca por coincidencia de texto sobre el nombre | §10.1 [real] |
| T-DATA-7 | Migración con `revision id` inventado a mano y no generado por la herramienta | generar siempre con `alembic revision` | §13.2 [real] |
| T-DATA-8 | Un bug real encontrado ejecutando contra TikTok se corrige sin test de regresión ni registro, y su causa raíz se pierde | registro estructurado de bugs + fila nueva en este apéndice en el mismo turno | §16 [real] |
| T-DATA-9 | Un evento declarado en el catálogo no tiene ningún productor real: nunca se emite y el test de paridad no lo detecta | cada evento del catálogo tiene su punto de emisión, y el test de paridad excluye del scan el propio archivo del catálogo | §13.2 [real] |
| T-DATA-10 | Recorrer el sistema de archivos para calcular el tamaño en disco de cada cuenta en cada regeneración del dashboard es una operación cara que escala mal con bibliotecas grandes | `total_disk_bytes` (§3.1) se mantiene de forma incremental en `handle_download_result` (§4.7); el dashboard (§10.3) solo lee esa columna, nunca recorre el disco | §10.3 [raz] |

### A.9 Migraciones, logging, despliegue y pruebas

| # | Qué falla | Regla | Origen |
|---|---|---|---|
| T-DEPLOY-1 | La migración repite `stamp` en cada comando | comprobar `alembic_version` antes de decidir `stamp` vs `upgrade` | §5.4 [real] |
| T-DEPLOY-2 | `env.py` síncrono con driver `aiosqlite` | template async / `connection.run_sync` | §5.4 [real] |
| T-DEPLOY-3 | Dos procesos migran a la vez | lock de fichero `.migrate.lock` | §5.4 [real] |
| T-DEPLOY-4 | Recurso localizado con ruta relativa al módulo se rompe en wheel o en la imagen | resolver por candidatos con error explícito | §5.4 [real] |
| T-DEPLOY-5 | El stage runtime solo copia el `.venv` y pierde `alembic.ini`/`alembic/` → `FileNotFoundError` en el primer arranque | copiar explícitamente los recursos no Python; verificar con un comando que migre, no con `--version` | §5.4/§14.1 [real] |
| T-DEPLOY-6 | `fileConfig()` de Alembic pisa el root logger: `docker logs` con 0 bytes y daemon healthy | reaplicar logging (`force=True`) inmediatamente después de migrar | §5.1 [real] |
| T-DEPLOY-7 | Healthcheck sin definición de "heartbeat fresco" | frescura ≤ 3 × intervalo configurado | §10.1 [real] |
| T-DEPLOY-8 | Configuración inválida deja el daemon arrancado a medias | `validate_for_daemon()` como primer paso | §5.1 [real] |
| T-DEPLOY-9 | Variable de `.env.example` sin efecto real | cableada de verdad o retirada | §11.1 [real] |
| T-DEPLOY-10 | Selfcheck sin sonda de `ffmpeg`/`ffprobe` | incluirlos como dependencia dura verificada | §4.1 [real] |
| T-DEPLOY-11 | `COPY . .` sin `.dockerignore` embebe secretos; y `.dockerignore` que excluye el README rompe el build del wheel | `.dockerignore` completo desde el primer commit y **re-incluir `README.md`** | §12/§15.3 [real] |
| T-DEPLOY-12 | Comentario inline dentro de una instrucción `ENV` multilínea → error de sintaxis del Docker | comentarios en líneas propias | §14.1 [real] |
| T-DEPLOY-13 | `restart: unless-stopped`: `daemon stop` sale con salida 0 y el contenedor vuelve a arrancar, así que la comprobación de que el daemon "se detiene de verdad" (§14.2) parece fallida aunque haya funcionado | `restart: on-failure`: una parada pedida por el operador queda parada, un crash (salida distinta de 0) se recupera | §14.1 [real] |
| T-DEPLOY-14 | Builder distroless de `uv`: el `.venv` copiado referencia un intérprete inexistente | patrón oficial: builder `python:3.13-slim` + `UV_PYTHON_DOWNLOADS=0` + `--no-editable` | §14.1 [real] |
| T-DEPLOY-15 | Línea parcial al final de `download_archive.txt` | parser tolerante que salta la última línea malformada | §3.6 [real] |
| T-DEPLOY-16 | Export CSV con inyección de fórmulas | `csv.writer` de stdlib + sanitización de operadores | §10.2 [real] |
| T-DEPLOY-17 | Un test consulta el entorno real (disco, red, reloj) y pasa o falla según la máquina | todo valor del entorno se inyecta o se mockea; los datos de test se calculan, nunca son absolutos | §13.1 [real] |
| T-DEPLOY-18 | Tests que escriben en rutas absolutas fijas, fuera de su `tmp_path` | los tests nunca escriben fuera de su `tmp_path` | §13.1 [real] |
| T-DEPLOY-19 | Datos de test dependientes del reloj real (epochs fijos) y dobles con firma incompleta | fechas futuras calculadas; dobles con la firma real completa (`**kwargs`) | §13.1 [real] |
| T-DEPLOY-20 | Un doble de test sin los kwargs nuevos enmascara un parámetro muerto, y el error real queda envuelto entre las excepciones de negocio | los dobles replican la firma real completa | §13.1 [real] |
| T-DEPLOY-21 | Un test de disco usaba el `disk_usage()` real del entorno con un umbral asumido: pasaba en una máquina y fallaba en otra | mockear `shutil.disk_usage` con un porcentaje libre controlado | §13.1 [real] |
| T-DEPLOY-22 | Un assert compara `str(Path)` (con `\` en Windows) contra una cadena con `/` | comparar objetos `Path`, nunca strings con separador fijo | §13.1 [real] |
| T-DEPLOY-23 | `COPY` con destino relativo `./` ejecutado antes de `WORKDIR /app` en el stage runtime: los archivos caen en `/` y el primer arranque muere con `alembic.ini not found` aunque las capas COPY existan en `docker history` | `WORKDIR /app` antes de todo COPY con destino relativo; test estático que exige `WORKDIR` precediendo a los COPY de alembic | §14.1 [real] |

### A.10 Clases de fallo que este diseño evita por decisión propia

No son trampas omitidas: son clases enteras de fallo que desaparecen porque la capacidad que las producía
quedó fuera del diseño o del alcance base.

| # | Clase de fallo evitada | Qué la producía | Decisión |
|---|---|---|---|
| — | Permisos laxos en una clave de cifrado, carrera al generarla en el primer arranque concurrente (archivo vacío leído en la ventana de creación), clave pasada por variable de entorno que queda inerte, y pérdida **irrecuperable** de las cookies si la clave se pierde | cifrado de las cookies en reposo | sin cifrado en reposo (§15.2, DR-3): menos código, menos superficie de fallo, misma protección real ante el modelo de amenazas |
| — | Diagnosticar un runner de CI que nunca se activa (billing, agente desconectado, secretos ausentes) — solo aplica si el operador decide activar CI | CI obligatorio con pipeline propio | CI opcional; si se activa, Woodpecker en exclusiva, con el gate local como respaldo siempre disponible (§14.4, DR-7) |

---

## Apéndice B — Verificado en línea (2026-09-24)

Todo lo de este apéndice es **perecedero**: es un hecho con fecha. Re-verificar antes de fijar cualquier
pin (§2.3). Fuera de esta lista, nada en el documento depende de una única fuente no verificada.

### B.1 Hechos verificados

| # | Hecho | Fuente |
|---|---|---|
| B.1.1 | `yt-dlp` declara `requires-python = ">=3.10"`, **sin techo superior** | `yt-dlp/pyproject.toml` (master) |
| B.1.2 | `yt-dlp` publica un extra `pin-curl-cffi` (además de `curl-cffi` y `pin`), que fija la versión exacta de `curl-cffi` de referencia de esa release; el extra `curl-cffi` por sí solo declara `>=0.5.10,<0.16` | `yt-dlp/pyproject.toml`; `devscripts/update_requirements.py` (`PINNED_EXTRAS`); PyPI de yt-dlp |
| B.1.3 | **El extractor de TikTok pasa `impersonate=True` en sus propias peticiones web** (3 sitios en `yt_dlp/extractor/tiktok.py`), y por lo tanto la impersonación TLS operativa es requisito para extraer de TikTok | `yt-dlp/yt_dlp/extractor/tiktok.py` (master) |
| B.1.4 | El extra `default` de yt-dlp incluye `yt-dlp-ejs==0.8.0`, y su documentación declara `ffmpeg`, `ffprobe`, `yt-dlp-ejs` y un runtime/motor de JavaScript soportado como *highly recommended* | PyPI de yt-dlp; `pyproject.toml` |
| B.1.5 | `_get_available_impersonate_targets` es **API privada** con un TODO explícito de upstream para hacerla pública; devuelve pares `(ImpersonateTarget, handler)` y el parámetro `impersonate` exige el **objeto** | commit `7bf1abb` de yt-dlp; PR #9474 |
| B.1.6 | `uv` soporta `prerelease-package` (per-package) desde 0.12.1 | docs de `uv` (resolution/settings/CLI) |
| B.1.7 | El challenge WAF de TikTok se resuelve con una **implementación nativa en Python** (`_solve_challenge_and_set_cookies`: bucle de hasta 10^6 de SHA-256, cookie `_wafchallengeid`), así que el camino de TikTok **no** necesita runtime de JS. El runtime externo es requisito de **YouTube** desde `2025.11.12` (challenges EJS): `deno >= 2.3`, `node >= 22`, `quickjs >= 2023-12-9`, `bun` deprecado; el extra `default` trae `yt-dlp-ejs` pero no el motor | `yt_dlp/extractor/tiktok.py` (master); wiki EJS de yt-dlp; anuncio del issue #15012 |

### B.2 Hechos verificados — librerías

| # | Hecho | Fuente |
|---|---|---|
| B.2.1 | `APScheduler` 3.11.3 es la serie estable; `4.0.0a*` sigue en pre-release y su documentación advierte explícitamente contra producción | PyPI/docs de APScheduler |
| B.2.2 | `SQLAlchemy 2.1.0rc1` publicado el 2026-08-31, no estable; el 2.0.x sigue siendo la serie estable, y 2.1 exige Python ≥3.11 | blog y GitHub de SQLAlchemy |
| B.2.3 | `typer` **no** tiene soporte async nativo (issue abierto); existe un paquete de terceros `async-typer` | issue #950 de typer; PyPI de `async-typer` |
| B.2.4 | `aiosqlite 0.22.0` introdujo un hang bajo SQLAlchemy async, mitigado en SQLAlchemy 2.0.51 y corregido en `0.22.1` | omnilib/aiosqlite#369 → sqlalchemy#13039 |
| B.2.5 | `uv` documenta `prerelease-package` en `[tool.uv]` como override por paquete de la política global (`prerelease = "disallow"` + `{ yt-dlp = "allow" }`), con modos `disallow`/`allow`/`if-necessary`/`explicit`; el modo `explicit` además restringe prereleases a paquetes con specifier explícito | docs de uv (`concepts/resolution.md`), vía context7 `/astral-sh/uv` **[2026-09-26]** |
| B.2.6 | pydantic-settings documenta `NoDecode` + `BeforeValidator` (split por comas, tipo anotado reutilizable) como el patrón oficial para campos complejos de entorno sin parseo JSON | docs de pydantic-settings (`Disabling JSON parsing`), vía context7 `/pydantic/pydantic-settings` **[2026-09-26]** — respalda la regla del `.env` de §14.6 |
| B.2.7 | PTB documenta el ciclo de vida manual (`initialize` → `start` → `updater.start_polling` → `shutdown`) como el camino soportado al integrar en un loop asyncio existente, y `ExtBot.initialize`/`shutdown` gestionan el rate limiter configurado | docs stable de PTB (`Application.run_polling > Integration`, `ExtBot`), vía context7 `/websites/python-telegram-bot_en_stable` **[2026-09-26]** |
| B.2.8 | SQLAlchemy con `aiosqlite` usa `AsyncAdaptedQueuePool` por defecto en archivos y `StaticPool` por defecto en `:memory:`; recomienda `NullPool` ante problemas de file locking o uso en múltiples event loops; existe patrón shared-cache in-memory URI para múltiples corrutinas | docs 2.0 de SQLAlchemy (`dialects/sqlite.html`, `pooling.html`), vía context7 `/websites/sqlalchemy_en_20` **[2026-09-26]** — respalda §3.7 y DR-11 |
| B.2.9 | El cookbook de Alembic documenta la migración programática async vía `conn.run_sync(...)` + `config.attributes["connection"]` como alternativa soportada al `to_thread` de T-ASYNC-4; el enfoque del plan sigue válido | docs de Alembic (`cookbook.html > Programmatic API use with Asyncio`), vía context7 `/websites/alembic_sqlalchemy` **[2026-09-26]** |
| B.2.10 | Las opciones de red de yt-dlp advierten que forzar `--impersonate` para todos los requests puede degradar velocidad y estabilidad de descarga; `--list-impersonate-targets` y el extra `curl-cffi` están documentados en el README | README de yt-dlp (`Network Options`, `Dependencies > Impersonation`), vía context7 `/yt-dlp/yt-dlp` **[2026-09-26]** — respaldo adicional para DR-4/T-ENGINE-11/T-ENGINE-15 |
| B.2.11 | APScheduler: la doc en master mantiene la advertencia de que la serie 4.0 es pre-release y "do NOT use this release in production", y aún no soporta importar jobstores persistentes de 3.x | docs de APScheduler (`versionhistory.rst`, `migration.rst`), vía context7 `/agronholm/apscheduler` **[2026-09-26]** — refuerza el pin 3.x de §2 |
| B.2.12 | curl-cffi documenta su guía de targets de impersonación con versiones vigentes (p. ej. `chrome146`, `safari260`) y alias genéricos (`chrome`, `firefox`, `safari`); la lista cambia con cada release, por lo que la sonda debe ser dinámica (§4.1) | docs de curl-cffi (`impersonate/targets.html`), vía context7 `/websites/curl-cffi_readthedocs_io_en` **[2026-09-26]** |
| B.2.13 | aiosqlite usa un hilo compartido por conexión con cola de requests serializada: es thread-safe por conexión y serializa operaciones concurrentes sobre la misma conexión | docs de aiosqlite (`Details`), vía context7 `/omnilib/aiosqlite` **[2026-09-26]** — respalda las reglas de sesión de §3.7 |

### B.3 Combinación de versiones objetivo

Combinación de versiones **compatible entre sí por lo declarado en cada proyecto** (B.1/B.2) y que este
plan fija como punto de partida — no una garantía permanente, sino el estado verificado a la fecha de
este documento (reverificar antes de fijar el pin, §2.3):
`yt-dlp 2026.9.16.232951.dev0` (nightly pineada vigente), `curl-cffi 0.16.0` vía el extra
`pin-curl-cffi`, `SQLAlchemy 2.0.54` (`>=2.0.51,<2.1`, DR-15), `alembic 1.19.1`, `aiosqlite 0.22.1`,
`APScheduler 3.11.3`, `python-telegram-bot 22.8`, `typer 0.27.1`, `rich 15.0.0`,
`pydantic 2.13.4`, `httpx 0.28.1`.

**Reverificación en vivo 2026-09-26 (PyPI + pyproject de yt-dlp master, antes del primer `uv lock` de
M0, §2.3):** la nightly `2026.9.16.232951.dev0` sigue siendo la más nueva publicada en PyPI y el extra
`pin-curl-cffi` sigue declarando `curl-cffi==0.16.0`; wheels de `curl-cffi` confirmados para
x86_64/aarch64 (manylinux + musllinux), win_amd64 y macOS. Constraints vigentes: `aiosqlite != 0.22.0`
(0.22.1), `SQLAlchemy >=2.0.51,<2.1` (2.0.54), `APScheduler >=3.11,<4` (3.11.3), prerelease acotado a
yt-dlp. Bumps menores disponibles: `alembic 1.20.0`, `typer 0.27.2`, `pydantic 2.13.5`,
`pydantic-settings 2.15.0`. **Punto B.4.1 resuelto a favor de no relajar**: `SQLAlchemy 2.1.1` ya es
estable, por lo que el techo `<2.1` pasa a ser decisión explícita — se mantiene en 2.0.x para M0 porque
la combinación B.3 está verificada tal cual y relajar no compra nada; reverificar antes de subir.

### B.4 Qué reverificar antes de empezar

**Reverificación parcial 2026-09-26 (vía context7, sin contacto en vivo):** los puntos 1 (DR-15 ya la
resuelve), 2, y los aspectos documentales de 3, 5 y 6 se refrescaron en el Apéndice B.2
(B.2.5–B.2.13). Lo que sigue exige verificación **en vivo** (PyPI/GitHub/entorno real):

1. Estado de `SQLAlchemy 2.1` (la final se anunciaba "en una semana" el 2026-08-31): si ya es estable, la
   restricción `<2.1` pasa a ser una decisión, no una obligación.
2. Estado de `APScheduler 4.x`: la doc oficial sigue advirtiendo contra producción (B.2.11); re-verificar el
   jobstore y el apagado recién si sale de pre-release.
3. yt-dlp nightly más reciente + confirmación del extra `pin-curl-cffi` (no documentado en el README, solo
   en `pyproject.toml`: verificar contra el fuente) y de la versión de `curl-cffi`.
4. **Techo de Python**: si las dependencias y el runtime lo permiten, revisar `requires-python` (el techo
   `<3.14` es autoimpuesto y bloquea upgrades).
5. Camino de TikTok en el extractor: confirmar que la nightly pineada mantiene el solver nativo del challenge (§2.2, T-ENGINE-30) y que ninguna actualización delega ese camino a un runtime de JavaScript.
6. Wheels de `curl-cffi` para la plataforma de despliegue (crítico en ARM64).

---

## Apéndice C — Decisiones de diseño

### C.1 Decisiones de diseño

| ID | Decisión | Alternativa descartada | Costo asumido |
|---|---|---|---|
| **DR-1** | El proyecto sigue en **Python** y conserva el nombre `tikdown-rs` | renombrar el proyecto; migrar a Rust | un nombre que no coincide con el lenguaje de implementación; se documenta explícitamente en el README, una sola vez, para evitar confusión. El nombre es una decisión de identidad del proyecto, independiente del lenguaje en el que está escrito — cambiarlo no aporta ningún beneficio funcional |
| **DR-2** | yt-dlp **nightly con pin exacto** | canal estable con rango | mantenimiento periódico obligatorio (bump + rebuild); se acepta porque el estable llega atrasado a los parches de extracción (§2.1) |
| **DR-3** | **Sin cifrado en reposo** para las cookies | cifrado simétrico con la clave en el mismo host/volumen (por ejemplo Fernet) | cookies en claro en la base y en sus backups; mitigado por control de acceso al host/volumen y por tratar los backups como secretos. La decisión se revisa si la base sale del host (§15.2). Una clave que vive en el mismo host/volumen que el secreto que protege no añade una barrera real ante el modelo de amenazas de §15.1, y sí añade superficie de fallo propia: una carrera de generación en el primer arranque concurrente, una variable de entorno que puede quedar inerte si no se cablea con cuidado, y una pérdida irrecuperable de las cookies si la clave se pierde |
| **DR-4** | El parámetro `impersonate` del motor queda **opt-in y apagado**; la impersonación que opera es la interna del extractor | forzar `impersonate` en todas las llamadas | menos control explícito sobre el TLS; forzarlo a nivel de parámetros reduce el listado del feed a 1 entrada y rompe la descarga (T-ENGINE-11), porque algunos targets de impersonación fallan contra esas rutas concretas y `ignoreerrors` los descarta en silencio. Es [H] y se re-mide |
| **DR-5** | `COOKIE_VALIDATION_URL` **sin default** y como lista de 2–3 perfiles propios | un perfil público de terceros fijo como default | configuración obligatoria en el primer arranque; se acepta por privacidad y porque un perfil ajeno puede morir o cambiar |
| **DR-6** | Jobstore del scheduler **en memoria** | `SQLAlchemyJobStore` persistente | re-registrar los jobs en cada arranque (trivial, son de intervalo simple); a cambio se evita la serialización pickle y el vector de deserialización |
| **DR-7** | CI **opcional** (gate local siempre obligatorio); si se activa, **Woodpecker autohospedado en exclusiva** — nunca GitHub Actions | CI obligatorio desde el primer commit, con publicación multi-arquitectura incluida | menos garantía automática por defecto para un solo desarrollador que no active CI; a cambio, ninguna infraestructura que mantener sin uso. La publicación de imágenes queda además fuera de alcance por separado (§17.3): activar CI no implica publicar |
| **DR-8** | Alcance base **con** dashboard estático de solo lectura (§10.3); **sin** push de notificaciones ni presupuesto de reintentos | mantener los tres fuera del alcance base, o los tres dentro | el dashboard entra porque es de solo lectura sobre datos ya calculados y no depende de ningún ciclo emisor que aún no exista (§0.3); push de notificaciones y presupuesto de reintentos sí dependen de un ciclo emisor/consumidor que todavía no existe en el alcance base, así que quedan como épicas con criterio de activación (§17) — declararlos antes produce canal noop, tabla de spool sin lecturas y funciones sin llamador |
| **DR-9** | La verificación incluye un **tier en vivo** explícito y separado | prohibir todo contacto con el entorno real, incluso en un tier separado | requiere cookies y red para ese tier; los fallos de integración aparecen en ejecución real, así que sin este tier la única ejecución real es la de producción |
| **DR-10** | Sin umbral de cobertura numérico | >85 % en puntos calientes | menos señal automática; se compensa con la lista de casos obligatorios (§13.2). Evidencia: las métricas altas convivieron con lógica sin llamador |
| **DR-11** | La condición "base en memoria" se deriva de la URL **parseada** (componente de base de datos vacío o `:memory:`), no de un patrón de texto | el patrón textual `"///" not in url` de T-DB-9, que no cubre `sqlite+aiosqlite:///:memory:` —lleva `///` y es en memoria— y hacía que cada conexión viera una base vacía distinta | una decisión acoplada a la API de URL de SQLAlchemy, que ya está en el camino crítico; se re-mide si esa forma cambia |
| **DR-12** | `stamp` exige que la base ya contenga la **tabla marcadora del proyecto**; con tablas ajenas se aplica `upgrade`. `stamp` apunta siempre a `MARKER_TABLE_REVISION` (nunca a `head`), seguido de `upgrade("head")` en el mismo paso | `stamp("head")` directo ante cualquier tabla sin `alembic_version`, o eliminar el camino de `stamp` por completo | un `stamp("head")` directo dejaría un techo real: una base con la tabla marcadora pero anterior a una migración posterior quedaría marcada en `head` sin recibirla (T-DB-10). El costo de cerrarlo de entrada es mínimo — una constante (`MARKER_TABLE_REVISION`) y ejecutar `upgrade` siempre, incluso tras un `stamp` que ya se asumía al día — así que se cierra desde el primer commit en vez de declararlo "inalcanzable hoy": esa clase de suposición es exactamente la que §0.1 pide no universalizar |
| **DR-13** | Política de reinicio del contenedor `on-failure` | `unless-stopped` | un crash con salida distinta de 0 se recupera, pero un `daemon stop` limpio queda detenido y no resurrecta; a cambio, el operador que quiera el daemon de vuelta tiene que levantarlo a mano, que es exactamente lo que pidió al detenerlo |
| **DR-14** | Una sola identidad de autoría canónica, `PansitoDeMichi <PansitoDeMichi@users.noreply.github.com>`, declarada en `[project] authors`, en el `LICENSE` y en la configuración de Git de cada clon | una identidad por máquina, o no declararla en ningún lado | la dirección es una *noreply* del proveedor, pública por diseño, así que no expone un correo real; a cambio hay que fijarla en cada clon, porque la identidad de Git no viaja con el repositorio |
| **DR-15** | Mantener `SQLAlchemy >=2.0.51,<2.1` aunque 2.1.0/2.1.1 ya son estables (verificado 2026-09-25: sqlalchemy.org blog + PyPI) | migrar a 2.1.x | la serie 2.0.x (2.0.52/2.0.54) es la combinación de versiones objetivo de este plan (B.3), fijada explícitamente en vez de tomar la última estable; el `<2.1` pasa de obligación a decisión explícita, reverificar en el próximo bump |
| **DR-16** | Toda columna NOT NULL de los singletons lleva `server_default` en DDL | confiar en los defaults de Python de SQLAlchemy | el `INSERT ... ON CONFLICT DO NOTHING` nativo no ejecuta defaults de Python; sin server_default el primer arranque revienta con `IntegrityError: NOT NULL failed` (bug real, test de regresión: test_migrations) |
| **DR-17** | Retirar `YTDLP_ANTIBOT_BACKOFF_BASE/CEILING_SECONDS` de la configuración base | mantener las variables sin consumidor | sin un bucle de reintento automático (§17.2) no tienen punto de efecto; el backoff anti-bot operativo es el cooldown global (§4.5) más la clasificación transitoria (§4.4). Se reintroducen junto con la épica de reintentos (T-DEPLOY-9: variable sin efecto se retira) |
| **DR-18** | Retirar el parámetro `retry_fn` de `handle_download_result`; el reintento de respuesta degradada de §4.7 vive **dentro** de `engine.download` | cablear un `retry_fn` real en monitor y backfill | la firma §4.7 declara un ganador de reintento que nunca tuvo llamador (§0.3): el embudo del motor ya ejecuta DEFAULT_FORMAT → FALLBACK_FORMAT con descarte de la entrada del archive antes del fallback (T-ENGINE-18), así que una tercera pasada del llamador con los mismos formatos no agrega nada. El punto de verdad conserva su deber T-ENGINE-18 propio: descartar la entrada del archive y persistir `failed/integrity` para que `retry-failed` pueda reintentar. Verificado en revisión M0-M4 (JD-A-003/JD-B-004) |

### C.2 Decisiones de modelo y de contrato que evitan fallos silenciosos

| # | Decisión | Motivo |
|---|---|---|
| C-1 | `videos.status` incluye `pending` y el flujo descubrir→descargar está definido en §3.3 | sin `pending` no hay forma de persistir un vídeo descubierto: el `INSERT` falla, el error queda como `WARNING` y el resultado es indistinguible de "la cuenta no subió nada" (T-DATA-1) |
| C-2 | La sonda de impersonación no depende de una única API y degrada la **operación**, no el proceso | `_get_available_impersonate_targets` es API privada de yt-dlp con un TODO explícito de upstream para hacerla pública: un cambio de forma no debe impedir operar (§4.1) |
| C-3 | La sonda distingue "no hay impersonación" de "no se pudo inspeccionar" | son dos diagnósticos distintos, y confundirlos convierte un problema de diagnóstico en una caída total |
| C-4 | `pending_notifications` y `MAX_VIDEO_*` no están en el esquema ni en la configuración base | pertenecen a capacidades fuera del alcance (§17); un campo sin productor es deuda que se paga en cada migración. `total_disk_bytes` y `STATIC_SITE_*` sí están en el alcance base (§3.1, §10.3) porque tienen productor real desde el primer commit: `handle_download_result` y el job de regeneración del dashboard |
| C-5 | El techo de reintentos y el presupuesto de tiempo por vídeo se implementan junto con el bucle de reintento automático, no antes | sin ese bucle no hay nada que acotar, y las funciones quedan sin llamador (§9.6, §17.2) |

---

**Fin del plan maestro.** Este documento es autosuficiente: contiene todo lo necesario para construir el
proyecto (CLI + daemon + bot de Telegram) desde cero, sin consultar ningún otro archivo ni depender de
ninguna decisión tomada fuera de él. Toda afirmación verificada lleva fuente y fecha en el Apéndice B;
toda hipótesis [H] es una medición que hay que repetir en el entorno real antes de universalizarla.

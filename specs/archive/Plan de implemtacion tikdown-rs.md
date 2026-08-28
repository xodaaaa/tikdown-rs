# TikDown-rs — Plan Maestro de Implementación (CLI + Daemon + Telegram)

> **Propósito de este documento**
> Esta es la **única** especificación de TikDown-rs: la Single Source of Truth del proyecto. Es un documento **autocontenido**: una inteligencia artificial implementadora **no debe asumir conocimiento de prototipos, discusiones previas, versiones anteriores ni documentos externos** — todo lo necesario está aquí dentro. Las referencias de la forma `§N` o `§N.M` apuntan siempre a secciones de este mismo documento.
>
> **Origen del contenido**: este documento consolida y sustituye toda la documentación anterior del proyecto (la especificación canónica, las convenciones de trabajo, la política de seguridad y la guía de despliegue de la implementación previa), auditado, ordenado y resuelto aquí. El proyecto se **reconstruye desde cero**: no existe código previo que conservar, pero sí existe conocimiento acumulado muy valioso — las trampas de implementación (§19), las decisiones de diseño (§20) y las lecciones de la implementación anterior (§21) — que este documento preserva íntegro para que la nueva implementación no repita errores ya diagnosticados.
>
> **Objetivo**: herramienta robusta, de un solo usuario, para archivar vídeos de TikTok de forma automatizada y resistente a bloqueos, controlable por terminal local y por Telegram, publicable en GitHub como proyecto open-source sin fugas de secretos.
>
> **Fecha de referencia del conocimiento**: 4 de agosto de 2026. Las versiones de librerías indicadas son un punto de partida razonado y verificado a esa fecha; el implementador **debe reverificar contra PyPI/GitHub** antes de fijar `pyproject.toml` — la tabla de §1 no es un mandato ciego (procedimiento de verificación en §1.2).
>
> **Jerarquía normativa interna**: la **política de seguridad (§22)** es transversal y **prevalece sobre cualquier otra sección** en caso de conflicto; sus resoluciones quedan registradas en §20. Las convenciones de trabajo (§24) definen cómo se trabaja; las secciones §0–§18 definen qué se construye y cómo se comporta.
>
> **Contenido añadido durante la consolidación**: los fragmentos que la documentación anterior tenía rotos o incompletos y han sido reconstruidos, así como cualquier aclaración añadida por el consolidador, aparecen marcados inline con la nota *[Añadido en la consolidación]* y están registrados por completo en §25.

---

## 0. Visión general y arquitectura

TikDown-rs se ejecuta en **dos modos** del mismo código base, sin ningún servidor HTTP ni frontend:

1. **`tikdown-rs daemon run`** — Proceso de larga duración (entrypoint de Docker). Ejecuta en un único event loop asyncio:
   - Scheduler APScheduler (`AsyncIOScheduler`) con jobs de intervalo simple.
   - Ciclo del monitor (descubrimiento de vídeos nuevos en cuentas en modo `monitor`).
   - Validación periódica de cookies y refresco de perfiles.
   - Probe de red.
   - Heartbeat de estado del daemon.
   - Bot de Telegram en long polling real, corriendo en el mismo event loop (sin hilos adicionales).

2. **`tikdown-rs <grupo> <comando>`** — Comandos de un solo disparo desde terminal (o `docker exec`). Cada comando abre su propia conexión a la base de datos SQLite (la misma que usa el daemon), ejecuta lógica de negocio a través de la capa `services/*` y termina. La mayoría de comandos **no requieren** que el daemon esté corriendo; solo las acciones de control del monitor (`monitor start/stop`) y la **ejecución** de backfills encolados necesitan un daemon vivo (§10: es el daemon quien recoge los `queued`).

**Coordinación entre CLI y daemon**: se resuelve **enteramente vía SQLite** (tablas `daemon_state` y `download_pacing_state` + WAL mode). No hay sockets, puertos ni servidor de control. Esto es intencional para simplicidad y fiabilidad en entorno homelab de un solo usuario. *Consecuencia a tener presente: al ser coordinación por base de datos, el estado en memoria del daemon (p. ej. estado de red) NO es visible desde otros procesos salvo lo que el daemon persiste explícitamente en su heartbeat — `daemon status` solo puede leer eso.*

**Telegram como interfaz remota principal**: cuando está habilitado (`TELEGRAM_BOT_MODE=commands|both`), el bot proporciona control remoto completo. Opt-in pero recomendado para uso fuera del host.

### Principios de diseño no negociables

1. Superficie de ataque mínima: sin servidor HTTP expuesto, sin frontend, sin autenticación basada en red.
2. `yt-dlp` + `curl-cffi` como **único** cliente contra dominios de TikTok — nunca `httpx`/`requests` directo a TikTok.
3. Todo async-first: llamadas bloqueantes de `yt-dlp` envueltas en `asyncio.to_thread`, y **también toda I/O pesada** (hashing SHA-256, `ffprobe`, lecturas/reescrituras de archivos grandes) va a `to_thread` — ver trampa T12.
4. Estado y coordinación centralizados en SQLite (WAL) para concurrencia CLI ↔ daemon.
5. Capa `services/*` 100% independiente de `cli/` y `daemon/` → reutilizable, testeable y compartida por el bot de Telegram. **Nunca** importa `yt_dlp`, `typer`, ni el SDK del bot directamente.
6. Fail-fast en arranque del daemon si la impersonación TLS no está operativa **o la configuración es inválida**.
7. Backfill con slot único global de concurrencia (por proceso).
8. Logging estructurado como fuente de verdad local. Implementación real: **logging stdlib con formatter JSON ad-hoc** (decisión F-20 de la auditoría de la implementación anterior: `structlog` fue declarado en el stack sin ningún consumidor y se retiró — ver §1 y §20). JSON a stdout en el daemon; consola legible en CLI. Nivel vía `LOG_LEVEL`.
9. Telegram como canal de **notificaciones exhaustivas**: todo lo accionable (problemas de cookies, formato, backfills, red, disco, etc.).
10. **Toda tarea en segundo plano pasa por un helper supervisado** (`create_supervised_task()`), nunca `asyncio.create_task` directo en ningún módulo del proyecto. Una tarea de fondo sin supervisión que falla en silencio es un bug de producción, no un detalle menor.
11. **Los fallos de TikTok se clasifican en tres categorías, nunca dos**: *definitivos* (auth inválida, cuenta suspendida, captcha, contenido inexistente), *transitorios* (rate-limit/anti-bot, timeouts de red, degradación puntual del extractor) e — específicamente para cookies — *inconcluso* (el intento no confirma ni descarta validez). Tratar un fallo transitorio como definitivo es la fuente más común de bloqueos autoinfligidos en este tipo de proyecto: invalida recursos que siguen siendo válidos y detiene el sistema innecesariamente. Este principio se aplica de forma consistente en el motor de descarga (§4), la validación de cookies (§7) y el circuit breaker (§4.3).
12. **Toda operación de limpieza posterior a un éxito ya confirmado es best-effort**: si falla, se registra como advertencia y el resultado global sigue siendo éxito. Nunca convertir un fallo de limpieza posterior en un fallo del comando completo (trampa T14).
13. **Ante una caída real de conexión a internet (no un bloqueo de TikTok), el daemon pausa automáticamente monitor, backfill y descargas, avisa por Telegram y reanuda solo al recuperar red**, sin intervención manual — ver §9.
14. **Toda ruta de datos deriva de `DATA_DIR`**: base de datos, clave Fernet, archive de deduplicación y vídeos comparten el mismo almacenamiento persistente. Jamás rutas relativas al directorio de trabajo (ver trampa T8, bloqueante).

### 0.1 Higiene de secretos y publicación en GitHub (requisito transversal, no una fase final)

Como el repositorio se publica en GitHub, el manejo de secretos se diseña desde el primer commit, no se "limpia" al final:

- **`.gitignore` desde el commit inicial**, cubriendo como mínimo: `.env`, `*.db`, `*.db-wal`, `*.db-shm`, `/app/data/` (o el `DATA_DIR` local equivalente en desarrollo), `videos/`, `fernet.key`, `*.session`, cualquier export de cookies (`cookies*.txt`, `cookies*.json`), y directorios de entornos virtuales.
- **`.dockerignore` OBLIGATORIO (trampa T15)**: el `COPY . .` de un Dockerfile sin `.dockerignore` embebe `.env`, el volumen de datos, `fernet.key`, bases de datos, cookies y `.git` en capas de la imagen — recuperables aunque se borren en una capa posterior. Debe cubrir como mínimo: `.env*`, `data/`, `videos/`, `*.db*` (incluyendo `*.db-journal`, `*.sqlite*`, `*.sqlite-wal`/`-shm`), `*.session*`, `fernet.key`, `cookies*.txt|json`, `.git/`, `.venv/`, `__pycache__/`. **Excepción verificada (F-04)**: `README.md` debe quedar **incluido** en la imagen (hatchling lo exige para construir el wheel en la segunda pasada de `uv sync`).
- **`.env.example`** con todas las variables de §12 documentadas y **solo valores de ejemplo o vacíos** — nunca un token, chat ID o clave real, ni siquiera "de prueba": un valor con forma realista invita a que alguien lo pegue sin cambiarlo.
- **Ningún secreto en el código fuente ni en tests**: los tests de cifrado de cookies (§14) usan una `FERNET_KEY` generada al vuelo en el fixture, nunca una constante hardcodeada que parezca una clave real.
- **README explícito** sobre qué NO commitear: el propio volumen de datos, cualquier archivo de cookies exportado del navegador, y el `.env` real usado en despliegue.
- **Disclaimer legal en el README** (estilo yt-dlp): la herramienta está pensada para archivar contenido propio o permitido; la responsabilidad sobre los ToS de TikTok y el copyright del contenido descargado recae en el usuario. Protege al proyecto ante reclamaciones DMCA al publicarse. Nota de naming: el sufijo `-rs` es histórico y el proyecto es Python — aclararlo explícitamente en el README para evitar confusiones.
- **Backup y recuperación de `fernet.key` (requisito de diseño, no una nota operativa aparte)**: es el único secreto que descifra todas las cookies almacenadas; si se pierde o se corrompe sin respaldo, **todas** las cookies en `encrypted_blob` quedan permanentemente irrecuperables y el sistema se degrada silenciosamente hasta que alguien intenta usarlas. Documentar explícitamente en el README:
  1. Que `fernet.key` (o `FERNET_KEY`) debe respaldarse fuera del volumen de datos y fuera del repositorio, en un gestor de secretos o almacenamiento cifrado separado del host.
  2. Un procedimiento de recuperación claro: sin el `fernet.key` original, la única salida válida es purgar la tabla `cookies` y reimportar cookies frescas — no existe "recuperación" del ciphertext sin la clave (procedimiento operativo en §23.5.2).
  3. **`daemon selfcheck` verifica crypto (trampa T16)**: intenta descifrar al menos una cookie almacenada con la clave activa, para detectar una clave incorrecta o rotada de forma temprana y explícita, en vez de que el síntoma aparezca como fallos de auth aparentemente aleatorios más adelante. Debe distinguir "tabla ausente" (esquema aún sin migrar → resultado informativo, no fallo) de un error real de permisos, corrupción o bloqueo (→ FAIL).
- **Historial limpio antes del primer push público**: si durante el desarrollo llegó a commitearse un secreto real en algún momento local, no basta con borrarlo en el commit siguiente — hay que reescribir el historial (`git filter-repo` o equivalente) antes de que el repositorio se haga público, porque un secreto en el historial de git sigue siendo recuperable aunque ya no esté en el HEAD.
- **Archivo `LICENSE` desde el commit inicial *[Añadido en el análisis posterior]***: el objetivo declarado del proyecto es "publicable en GitHub como proyecto open-source" (§ preámbulo), pero ninguna sección fijaba una licencia — sin `LICENSE` en la raíz, un repositorio público en GitHub es legalmente "todos los derechos reservados" por defecto y nadie puede reutilizar el código pese a la intención declarada. Recomendación por defecto: **MIT** (mínima fricción, coherente con el resto del ecosistema `yt-dlp`/homelab que el proyecto consume); si el propietario prefiere copyleft, **GPL-3.0** es la alternativa razonable dado que el proyecto depende de `yt-dlp` (Unlicense/sin licencia formal). Añadir el archivo `LICENSE` a la estructura de §13 y una línea en el README apuntando a él.

---

## 1. Stack tecnológico

| Categoría | Elección | Notas / versión de referencia (reverificar antes de fijar) |
|---|---|---|
| Lenguaje / runtime | **Python 3.13** | Fijar el mismo minor en dev y Docker (`.python-version` con `uv`). Si el objetivo de despliegue es Debian 13 (Trixie): solo trae `python3.13` empaquetado (`python3` = 3.13.5; Debian retiró el paquete 3.12 — verificado 2026). No bajar de 3.11. |
| Gestor de paquetes | `uv` (≥0.12) | `uv sync`, `uv run`, `uv lock`. **Trampa T2**: el prerelease debe restringirse SOLO al paquete que lo necesita (`prerelease-package = { "yt-dlp" = "allow" }`); un `prerelease = "allow"` **global** en `pyproject.toml` permite que cualquier paquete (p. ej. `pydantic`) resuelva a una alpha en el lock — riesgo de cadena de suministro (confirmado en la documentación oficial de uv: el modo global se aplica también a dependencias transitivas). |
| CLI framework | `typer` (≥0.27, sobre Click) | **Sin soporte nativo de comandos async** (confirmado: el auto-wrap en `asyncio.run()` sigue sin implementarse upstream — PR #444 abierto): todos los comandos son funciones síncronas que envuelven la corrutina con `asyncio.run()` — centralizar ese wrapper en `cli/common.py` (trampa T18). Grupos registrados con `app.add_typer(grupo, name="sustantivo")` explícito. El `@app.callback()` global con `--version` e `invoke_without_command=True` es obligatorio (sin él, `tikdown-rs --help` lanza `RuntimeError: Could not get a command for this Typer instance` — lección L-A1, §21). |
| Salida terminal | `rich` (≥15) | Tablas, barras de progreso, paneles. **Trampa T3 (ver §4.8)**: los campos propios de una barra de progreso se acceden con `{task.fields[clave]}` (corchetes) — `{task.fields.clave}` lanza `AttributeError` en el primer render, no en la construcción. Además `total`/`completed` son kwargs reservados por `update()`: un campo de negocio con ese nombre muta la barra real en vez de solo mostrarse. Salida CLI con **marcadores ASCII puros** (OK/ERROR, `-`, `!`) — los glifos Unicode (`✓`, `✗`, `—`, `⚠`, `sí`) revientan en consolas Windows legacy con `UnicodeEncodeError` (lección L-A5, §21). |
| ORM / DB | SQLAlchemy 2.x (async) + Alembic | Fijar la serie `2.0.x` estable (no `2.1` mientras siga en beta). WAL activado (`PRAGMA journal_mode=WAL`). |
| Driver SQLite async | `aiosqlite` | Excluir explícitamente `==0.22.0` y exigir `SQLAlchemy>=2.0.51`. **Caso verificado**: `aiosqlite 0.22.0` introdujo un hang bajo SQLAlchemy async (omnilib/aiosqlite#369 → sqlalchemy#13039; la regresión se confirmó por el cambio del worker thread en 0.22.x y la mitigación en SQLAlchemy 2.0.51 / fix `380c234`), corregido en `0.22.1` — que además exige cierre explícito de conexiones, mitigado por el diseño de sesiones cortas (§16). |
| Validación / config | Pydantic v2 + `pydantic-settings` | Declarar `pydantic-settings` como dependencia explícita en `pyproject.toml` — no asumir que viene arrastrada solo por `pydantic`. |
| Descarga / extracción | `yt-dlp[default,curl-cffi]` — **canal nightly, no estable** | **Decisión de diseño, no un detalle de instalación**: pinear siempre contra el canal **nightly** de yt-dlp, nunca el estable. Motivo: TikTok rompe su frontend con frecuencia suficiente para que el canal estable llegue sistemáticamente "atrasado" frente a los parches de extracción; el nightly los publica el mismo día, y también recibe antes los parches de seguridad. El nightly se publica como *dev release* del propio paquete `yt-dlp` en PyPI (confirmado: wheels `2026.7.23.234303.dev0` en PyPI; instalable con herramientas estándar de Python). Instalación con `uv`: `uv add "yt-dlp[default,curl-cffi,pin-curl-cffi]==<fecha-nightly>" --prerelease=allow`. El extra `pin-curl-cffi` pinea `curl-cffi` (y sus transitivas) a la versión exacta que el propio yt-dlp mantiene como referencia para esa release (confirmado en el pyproject upstream: `curl-cffi==0.15.0` en la release actual) — usarlo en vez de un pin manual propio siempre que esté disponible; el pin manual queda como fallback. Fijar el identificador de fecha exacto (formato `AAAA.MM.DD.HHMMSS`) en `pyproject.toml`, nunca un rango abierto — un nightly es por definición menos probado que un estable, así que la reproducibilidad del build importa más aquí que en cualquier otra dependencia. Añadir `[tool.uv] prerelease-package = { "yt-dlp" = "allow" }` (no `prerelease = "allow"` global — trampa T2). **Trampa T4**: en PyPI el nightly se normaliza a PEP 440 (`YYYY.M.D.HHMMSS.dev0`, sin ceros a la izquierda); la versión **interna** del módulo (`yt_dlp.version.__version__`) coincide con el tag de GitHub `YYYY.MM.DD.HHMMSS` — usar siempre la interna al comparar contra la API de GitHub para detectar actualizaciones, nunca la del gestor de paquetes. |
| Impersonación TLS | `curl-cffi` — **pin exacto**, no rango | Pieza más frágil de todo el stack. **Trampa T6 (crítica)**: verificar en el momento de implementar qué serie de `curl-cffi` soporta la nightly de yt-dlp pineada (yt-dlp mantiene un guard de compatibilidad de versión máxima); un pin abierto o una serie no soportada hace que **todos** los targets de impersonación aparezcan `(unavailable)` → 403 silenciosos en cada descarga, sin ningún error explícito hasta el selfcheck. Fijar un pin **exacto** (`==x.y.z`), nunca `>=` — preferentemente gestionado por el propio extra `pin-curl-cffi` de yt-dlp, que ya fija la versión exacta soportada y la actualiza con cada nightly. Con `prerelease-package` mal configurado (T2) también pueden colarse betas del propio `curl-cffi` — pin exacto cierra ambos problemas a la vez. ARM64/aarch64: verificar disponibilidad de wheels precompiladas para la combinación exacta de versiones antes de desplegar (ver §4.1). |
| Procesamiento vídeo | `ffmpeg` / `ffprobe` (binario de sistema) | Invocados por `yt-dlp` y para verificación de integridad. **Dependencia dura** (T46): el daemon **no arranca** sin ellos. Invocar siempre con lista de argumentos + `--` antes de la ruta del archivo (trampa T13: un nombre de archivo que empiece por `-` se interpreta como opción). |
| Cifrado cookies | `cryptography` (Fernet) | Único secreto persistente en reposo — mantener actualizada por motivos de seguridad, no solo de features. **Trampa T7**: verificar permisos `0600` también sobre una clave **ya existente** al cargarla (no solo al generarla) — una clave con `0644` deja las cookies descifrables por otros usuarios locales del host; corregir a `0600` con warning si no es posible. |
| Scheduler | APScheduler 3.x (`AsyncIOScheduler`) | Pinear `>=3.11,<4`. No usar la serie 4.x: sigue en pre-release (`4.0.0a6`, verificada 2026; su documentación advierte explícitamente contra uso en producción). Jobstore en memoria (§5.4). En el apagado, el drenaje real de los jobs lo hace el registro de tareas supervisadas (§5.2, §5.7): con `AsyncIOScheduler`, `shutdown(wait=True)` **no** espera a los jobs en curso — los cancela (trampa T9; la semántica de cancelación se mantiene en la serie 4, cuyo `stop()` cancela el cancel scope del task group — la disciplina de drenaje de §5.2 es válida para ambas series). |
| Bot de Telegram | `python-telegram-bot[rate-limiter]` (async, ≥22) | Elección única y no negociable del proyecto, por su modelo de comandos declarativo y madurez para bots de control remoto tipo "CLI remota". No evaluar frameworks alternativos. **El extra `[rate-limiter]` es obligatorio** (instala `aiolimiter`): sin él, `AIORateLimiter` lanza `RuntimeError` al construir el bot (lección L-H2, §21; requerido por T41, §6.3). **Trampa T10**: no usar `run_polling()` dentro de un event loop ya existente (lanza `RuntimeError: event loop already running`) — usar `await app.initialize(); await app.start(); await app.updater.start_polling(timeout=25)`, y en el apagado `updater.stop() → stop() → shutdown()` (secuencia confirmada en la documentación oficial de PTB, patrón "manual Application lifecycle"). |
| HTTP cliente genérico | `httpx` (async) | Solo Bot API de Telegram y probe de red. **Nunca** contra dominios de TikTok. |
| Logging | **logging stdlib + formatter JSON ad-hoc** | El stack histórico declaraba `structlog`; la auditoría de la implementación anterior (F-20) demostró que no tenía ningún consumidor y se retiró. La implementación es logging stdlib con un formatter JSON propio. JSON a stdout en el daemon; consola legible en CLI. Nivel vía `LOG_LEVEL` (§12). **Trampa T72**: la migración Alembic (`fileConfig`) pisa el root logger — reaplicar el setup de logging tras migrar (§5.1). |
| Lint / format | `ruff` | `ruff check && ruff format` en CI y pre-commit (config materializada en `.pre-commit-config.yaml`, F-22). |
| Testing | `pytest` + `pytest-asyncio` + `coverage` | Exclusivamente mocks; nunca llamadas reales a TikTok (ver §14). Cobertura de referencia razonable: ~75-80% total, con los puntos calientes (motor de descarga, cookies, backfill, monitor) por encima del 85% — los handlers del bot, al ser delgados sobre `services/*`, pueden quedar por debajo sin que sea un problema real. |

**Eliminado intencionalmente**: cualquier framework web, servidor HTTP, frontend, sesiones o autenticación basada en red, Redis. La seguridad depende de:
- Acceso físico/shell al host o `docker exec`.
- `TELEGRAM_CHAT_ID` permitido (cuando el bot está en modo `commands`).
- Cifrado en reposo de cookies (Fernet).

### 1.1 Tabla de verificación vigente de versiones (verificada 2026-08-03)

> El plan exige **reverificar versiones contra PyPI/GitHub antes de fijar `pyproject.toml`** (procedimiento en §1.2). Esta tabla es el último estado verificado; reproducir el mismo procedimiento ante cualquier cambio de pin.

**Runtime (`pyproject.toml`)**:

| Paquete | Pin / restricción | Nota de verificación |
|---|---|---|
| python | `>=3.13,<3.14` | 3.13.14 en el entorno de referencia; fijar minor en `.python-version` |
| yt-dlp | `==<fecha-nightly>` exacta, p. ej. `2026.7.23.234303.dev0` | El nightly se publica en PyPI como dev release (verificado). **T4**: comparar versiones siempre con `yt_dlp.version.__version__`, nunca con la del gestor |
| yt-dlp extras | `[default,curl-cffi,pin-curl-cffi]` | Receta validada con `uv lock` (T2/T6); el extra `pin-curl-cffi` existe upstream y fija `curl-cffi` y sus transitivas |
| curl-cffi | `==0.15.0` vía extra `pin-curl-cffi` | ⚠ `0.16.0` (última en PyPI a la fecha de verificación) es incompatible con yt-dlp (`<0.16`); nunca un pin abierto |
| aiosqlite | `>=0.22.1` (excluir `==0.22.0`) | Regresión de hang documentada (ver §1, fila driver SQLite) |
| SQLAlchemy | `>=2.0.51,<2.1` | 2.0.51 era la última estable; 2.1 seguía fuera |
| APScheduler | `>=3.11,<4` | 3.11.3 última; 4.x sigue en pre-release en PyPI (4.0.0a6) |
| python-telegram-bot | `[rate-limiter]>=22` | 22.8 verificada; el extra instala `aiolimiter<1.3,>=1.1` (T41, L-H2) |
| typer | `>=0.27` | 0.27.1; sin soporte async nativo → wrappers `asyncio.run()` en `cli/common.py` (T18) |
| rich | `>=15` | 15.0.0 |
| pydantic / pydantic-settings | `>=2.13` / `>=2.14` | declarar `pydantic-settings` explícito |
| cryptography | `>=50` | Fernet |
| httpx | `>=0.28` | solo Bot API y probe de red — nunca TikTok |
| alembic | `>=1.18` | |

`[tool.uv]` requerido:

```toml
[project]
dependencies = ["yt-dlp[default,curl-cffi,pin-curl-cffi]==2026.7.23.234303.dev0", ...]

[tool.uv]
prerelease-package = { "yt-dlp" = "allow" }   # NUNCA prerelease global (T2)
```

**Desarrollo**:

| Herramienta | Versión verificada | Uso |
|---|---|---|
| `uv` | 0.12.1 | `uv sync`, `uv run`, `uv lock` |
| `ruff` | 0.16.1 | `ruff check && ruff format` en CI y pre-commit |
| `pytest` + `pytest-asyncio` + `coverage` | 9.1.1 / 1.4.0 / 7.15.3 | suite completa con mocks; cobertura por puntos calientes |

### 1.2 Procedimiento de reverificación de versiones (obligatorio ante cualquier cambio de pin)

*[Añadido en la consolidación: el procedimiento estaba referenciado pero nunca escrito explícitamente; se reconstruye a partir de las notas de verificación dispersas en §1 y §1.1.]*

1. **Runtime y herramientas**: consultar PyPI (o GitHub releases) para cada fila de §1.1 y anotar la última versión disponible: `uv pip index versions <paquete>`, la página de PyPI del proyecto, o `gh release list` para herramientas de GitHub.
2. **yt-dlp nightly**: listar las dev releases del paquete `yt-dlp` en PyPI y elegir la nightly más reciente en formato `YYYY.MM.DD.HHMMSS`; confirmar que el tag gemelo existe en GitHub (`YYYY.MM.DD.HHMMSS`) y que su pyproject upstream declara el extra `pin-curl-cffi` con la versión exacta de `curl-cffi` soportada.
3. **curl-cffi**: verificar que la serie pineada por el extra `pin-curl-cffi` de la nightly elegida sigue siendo la soportada (yt-dlp mantiene un guard de versión máxima) y que hay wheels para las plataformas objetivo (amd64/arm64).
4. **Restricciones conocidas que no deben relajarse sin motivo**: `aiosqlite != 0.22.0`; `SQLAlchemy >=2.0.51,<2.1`; `APScheduler >=3.11,<4` (4.x en pre-release); `python >=3.13,<3.14`.
5. **Regenerar el lock**: `uv lock` (con `prerelease-package` solo para yt-dlp, T2) y ejecutar la suite completa + selfcheck antes de dar el pin por válido.
6. Actualizar la tabla §1.1 con la nueva fecha de verificación.

### 1.3 Herramientas complementarias recomendadas (sin sobre-ingeniería)

- **Git hooks pre-commit**: `ruff check` + `ruff format` antes de cada commit. Config materializada en `.pre-commit-config.yaml` (F-22); instalar con `uv tool install pre-commit && pre-commit install`.
- **CI Woodpecker CI**: ruff, pytest, cobertura y build Docker multi-arquitectura (`buildx --platform linux/amd64,linux/arm64`) + smoke `docker run --rm … tikdown-rs --version` (F-22). La pipeline vive en `.woodpecker.yml` en la raíz del repositorio. Lección operativa asociada: L-K4 (§21) — un fallo de CI en 0s en todos los workflows puede ser billing o configuración del runner, no el código.
- **Conventional Commits** para los mensajes de commit + **`gh` CLI** para PRs (flujo completo en §24.5).
- **CLI `sqlite3`** para inspección ad-hoc de la base de datos en desarrollo.
- **Diferido explícitamente** (backlog §18): mypy/pyright. No añadir en el MVP.
- **Auditoría de vulnerabilidades de dependencias, ligera y periódica *[Añadido en el análisis posterior]***: §22.6 exige pines exactos para lo crítico pero el plan no definía cómo detectar CVEs nuevas en dependencias ya pineadas. Sin sobre-ingeniería (nada de servicios externos ni bots de PRs automáticos, que chocarían con el flujo de pin manual de §1.2/§4.1): añadir un job **programado semanal** en `.woodpecker.yml` (separado del pipeline de push/PR, §24.5.4) que ejecute `uv run pip-audit` (o `uv tool run pip-audit`) contra el `uv.lock` y notifique el resultado (fallo del job = revisar). Para la imagen Docker: `trivy image tikdown-rs:latest` en el mismo job programado, ya que una imagen puede introducir CVEs del sistema operativo base (`python:3.13-slim`) que `pip-audit` no ve. Ninguno de los dos bloquea el pipeline de PR (evita fricción diaria); ambos son solo del job semanal. Este hallazgo se registra como trampa nueva: **T76** — un pin exacto (§1, §22.6) protege de romper compatibilidad, no de una CVE publicada después del pin; sin una comprobación periódica, una dependencia pineada y "estable" puede llevar meses con una vulnerabilidad conocida sin que nadie se entere.

---

## 2. Modelo de datos

La base de datos de negocio (`<DATA_DIR>/tikdown-rs.db`) usa Alembic para migraciones. El implementador define columnas, índices y constraints exactos, pero debe soportar todo lo descrito.

### `monitored_accounts`
- `id` INTEGER PK
- `username` TEXT UNIQUE NOT NULL (sin `@`)
- `mode` TEXT NOT NULL DEFAULT `'history'` — `'history'` | `'monitor'`
- `paused` BOOLEAN NOT NULL DEFAULT 0
- `needs_review` BOOLEAN NOT NULL DEFAULT 0
- `notify_on_download` BOOLEAN NOT NULL DEFAULT 0
- `monitor_after_backfill` BOOLEAN NOT NULL DEFAULT 0 — si 1, al completar el backfill la cuenta pasa automáticamente a `mode='monitor'` (transición consumible, §10)
- `backfill_status` TEXT NOT NULL DEFAULT `'idle'` — `'idle'|'queued'|'backfilling'|'paused'|'completed'|'failed'|'cancelled'`. **El CHECK constraint incluye `'cancelled'` desde el primer esquema** (sin él, `backfill cancel` falla con `IntegrityError: CHECK constraint failed` — lección L-F7, §21). Nota de consistencia: `'paused'` es un valor **reservado** del esquema que hoy no produce ninguna ruta (los backfills interrumpidos por crash/apagado vuelven a `'queued'`, F-10); no debe implementarse un productor de `'paused'` salvo que se retome el backlog §18.
- `backfill_cursor` TEXT — cursor por `upload_date`, ver §10
- `backfill_total` INTEGER DEFAULT 0 — persistido al iniciar cada pasada (F-09)
- `backfill_done` INTEGER DEFAULT 0
- `last_check_at` TEXT — ISO8601 UTC, throttle 30s
- `follower_count`, `following_count`, `total_likes`, `video_count` INTEGER
- `profile_last_refreshed` TEXT
- `created_at`, `updated_at` TEXT

### `videos`
- `id` INTEGER PK
- `tiktok_video_id` TEXT UNIQUE NOT NULL
- `account_id` INTEGER FK → `monitored_accounts`
- `url`, `title`, `description` TEXT
- `duration` INTEGER
- `upload_date` TEXT — formato canónico `YYYYMMDD` (trampa T43)
- `local_path` TEXT — **absoluto, derivado de `DATA_DIR`** (trampa T8)
- `file_size` INTEGER
- `file_hash` TEXT — SHA-256 del archivo descargado
- `status` TEXT NOT NULL DEFAULT `'downloaded'` — `'downloaded'|'failed'|'cancelled'|'skipped'` (`'skipped'` = post no descargable por naturaleza — p. ej. slideshow/foto con solo pista de audio; **no** es un fallo: no cuenta para reintentos, ni para `retry-failed`, ni para el circuit breaker; ver §4.6. `'cancelled'` = descarga interrumpida por apagado, §5.2: **no** es terminal para el cursor ni dominio de `retry-failed` — su reintento lo produce la re-ejecución del backfill o la diferencia de conjuntos del monitor, §10)
- `downloaded_at` TEXT
- `retry_count` INTEGER DEFAULT 0
- `error_message` TEXT
- `error_category` TEXT — `'definitive'|'transient'|'integrity'` (ver §4), permite que `backfill retry-failed` filtre inteligentemente
- `created_at`, `updated_at` TEXT

### `cookies`
- `id` INTEGER PK
- `label` TEXT
- `encrypted_blob` **BLOB / LargeBinary NOT NULL** — ciphertext Fernet. *Importante: debe mapearse a un tipo binario real (`LargeBinary` en SQLAlchemy), nunca a `Text`, ya que el ciphertext de Fernet son bytes, no texto UTF-8 válido.*
- `expiration_date` TEXT
- `last_validated_at` TEXT — **solo se actualiza con resultado `valid`/`invalid`** (un `inconclusive` no desarma la revalidación activa — F-16)
- `validation_state` TEXT NOT NULL DEFAULT `'valid'` — `'valid'|'invalid'|'inconclusive'` (ver §7 — **no** un booleano `is_valid`: un resultado inconcluso debe poder registrarse sin marcar la cookie como inválida ni como confirmada, para permitir autocuración en el siguiente ciclo)
- `last_validation_reason` TEXT — motivo legible del último resultado (`login_required`, `captcha`, `banned`, `rate_limited`, `extractor_error`, `ok`, ...)
- `created_at`, `updated_at` TEXT

### `daemon_state` (singleton, `id=1` siempre, `CHECK (id = 1)`)
- `id` INTEGER PRIMARY KEY
- `monitor_running` BOOLEAN NOT NULL DEFAULT 0
- `stop_requested` BOOLEAN NOT NULL DEFAULT 0
- `daemon_pid` INTEGER
- `daemon_started_at` TEXT
- `last_heartbeat_at` TEXT — actualizado cada `HEARTBEAT_INTERVAL_SECONDS` (default 10s)
- `db_busy_count_5min` INTEGER DEFAULT 0 — contador rotativo de contención SQLite, ver §5.8
- `downloads_paused` BOOLEAN NOT NULL DEFAULT 0 — pausa global de las rutas de descarga por disco lleno (§4.3 punto 6, trampa T45); el motor la consulta junto a `network_available.wait()` antes de cada intento
- `last_known_good_ytdlp_version` TEXT — última versión de yt-dlp que superó el selfcheck completo (§4.1, F-14)
- `last_notified_ytdlp_version` TEXT — última versión por la que se emitió `monitor.yt_dlp_update_available` (dedupe por versión, §8)
- `last_selfcheck_at` TEXT / `last_selfcheck_ok` BOOLEAN — resultado del último selfcheck periódico persistido (§5.1, §15); lo muestra `daemon status` (`daemon healthcheck` es solo frescura de heartbeat, §3)

**Trampa T17**: la creación del singleton debe ser idempotente bajo concurrencia — `INSERT ... ON CONFLICT DO NOTHING` y releer. La migración crea la tabla pero **no** inserta la fila; dos procesos arrancando a la vez podrían violar la PK si se usa un `INSERT` simple.

### Otras
- `download_archive.txt` como archivo append-only (`<DATA_DIR>/download_archive.txt`) para `--download-archive` de yt-dlp, complementado por una tabla `download_archive` en SQLite (solo `tiktok_video_id`) como fuente de verdad consultable rápidamente desde `services/*` — ver §4.5 sobre el método de escritura y de descarte de entradas.
- `download_pacing_state` (singleton, `id=1`, mismo patrón `CHECK (id = 1)` + creación idempotente con `INSERT ... ON CONFLICT DO NOTHING` que `daemon_state`, T17): `next_allowed_at` TEXT (ISO8601 UTC con **precisión de milisegundos** — `timespec="milliseconds"`, sigue siendo lexicográficamente comparable; con precisión de segundos, dos reservas rápidas podían colapsar al mismo valor y romper el CAS — lección L-C7, §21) — reloj de pacing del cooldown global **compartido entre procesos** vía SQLite (§4.5, trampa T22). **El `INSERT ... ON CONFLICT DO NOTHING` del singleton debe commitearse de inmediato** (lección L-C6, §21): sin commit, la sesión hace rollback al salir, la fila nunca existe y el cooldown cross-proceso queda roto en silencio (fail-open permanente).
- No se usa jobstore persistente de APScheduler (ver §5.4).
- `pending_notifications` (spool de notificaciones, §8 y §9): `id` INTEGER PK, `event` TEXT, `payload` TEXT (JSON del evento original, **no el texto renderizado** — F-06), `created_at` TEXT. Acotada (FIFO, p. ej. 100 filas; se descartan primero las no críticas). Se drena en la transición a `online` y en el arranque del daemon.

**Índices recomendados**: `username`, `tiktok_video_id`, `account_id + downloaded_at`, `backfill_status`, `validation_state`.

---

## 3. Comandos CLI (interfaz principal local)

Todos los comandos usan `rich` para salida humana y soportan `--json` para scripting y para que el bot de Telegram reutilice las mismas funciones de `services/*`.

**Principio de diseño de la CLI (no negociable)**: la interfaz se organiza en exactamente **7 grupos de sustantivo** (`daemon`, `monitor`, `accounts`, `backfill`, `cookies`, `videos`, `system`). No existen comandos-verbo sueltos en la raíz (`tikdown-rs stats`, `tikdown-rs disk`, `tikdown-rs last`, etc.) — todo comando pertenece a un grupo, incluso si ese grupo termina teniendo un solo subcomando (`system disk`, `system backup`). Esto hace la CLI predecible y con mejor autocompletado (`tikdown-rs <TAB>` siempre muestra sustantivos, nunca una mezcla de sustantivos y verbos). Dentro de cada grupo, los verbos antónimos usan pares simétricos (`pause`/`resume`, no `pause`/`enable`) y el verbo de borrado es siempre `remove` (no `rm`, no `delete`) para consistencia entre grupos.

| Grupo | Comando | Descripción |
|---|---|---|
| `daemon` | `run` | Arranca el daemon (entrypoint). El daemon **nunca hace fork**: corre siempre en foreground; ejecutarlo en background es responsabilidad del orquestador (Docker), no de un flag de la CLI. |
| `daemon` | `stop` | Pide apagado limpio vía `stop_requested` en SQLite. |
| `daemon` | `status` | Estado del daemon + monitor + heartbeat + resultado del último selfcheck (`last_selfcheck_ok`) + tareas supervisadas activas + hilos zombis de yt-dlp activos (T66) + **contador de contención leído de `daemon_state`**, nunca del proceso CLI propio (trampa T19). |
| `daemon` | `selfcheck` | Verifica impersonación TLS (§4.1) **+ ffmpeg/ffprobe (T46) + clave Fernet + descifra una cookie almacenada** (trampa T16). |
| `daemon` | `healthcheck` | Para `HEALTHCHECK` de Docker: daemon vivo = **heartbeat fresco** (`last_heartbeat_at` ≤ 3 × `HEARTBEAT_INTERVAL_SECONDS` configurado — trampa T50). Exit 0/1. **No** ejecuta migraciones ni toma `.migrate.lock` (§5.5, R10 — corre cada ~30 s). **No** ejecuta el selfcheck completo en cada intervalo: instanciar `YoutubeDL` + abrir la base de datos + descifrar una cookie cada ~30s es coste innecesario y superficie de falsos negativos. El selfcheck corre en el arranque (§5.1) y como job periódico con resultado persistido en `daemon_state` (§5.1, §15), consultable vía `daemon status` y bajo demanda con `daemon selfcheck`. |
| `monitor` | `start` / `stop` | Inicia/detiene el ciclo del monitor (requiere daemon vivo; el monitor arranca siempre detenido). Escribe el flag en `daemon_state`; el heartbeat del daemon aplica el cambio en caliente (§5.1). |
| `accounts` | `add @user [--mode history\|monitor] [--then-monitor]` | Añade cuenta (`history` por defecto). `--then-monitor` (solo con modo `history`): al completar el backfill, la cuenta pasa sola a modo `monitor` (§10). **No arranca el monitor global** — solo cambia el `mode` de la cuenta; el monitor sigue detenido por defecto hasta `monitor start` (§5.1, T60). |
| `accounts` | `list` | Lista cuentas con estado y conteos. |
| `accounts` | `pause @user` / `resume @user` | Pausa/reactiva cuenta (par simétrico). |
| `accounts` | `notify @user --on/--off` | Activa/desactiva notificación por descarga. |
| `accounts` | `remove @user` | Borra cuenta (con confirmación). |
| `accounts` | `check @user` | Fuerza comprobación manual (respeta throttle 30s) con **motor y clave reales**, nunca simulados (trampa T20). |
| `accounts` | `stats @user` | Estadísticas de una cuenta. |
| `backfill` | `run @user [--queue]` | Lanza backfill. Sin `--queue`: foreground (proceso CLI, con barra de progreso). Con `--queue`: encola para el daemon (el daemon lo recoge con su slot; ver §10). |
| `backfill` | `status @user` | Progreso del backfill. |
| `backfill` | `cancel @user` | Cancela backfill activo (cancelación real, trampa T21). |
| `backfill` | `retry-failed @user` | Reintenta los vídeos `status='failed'` de la cuenta, descartando primero su entrada del archivo de deduplicación. `@user` es **obligatorio**: la variante global exige el flag explícito `--all` con resumen previo (vídeos × cuentas y duración estimada) y confirmación — un reintento global es un lote de horas o días, no una operación casual (la tasa de requests ya la acota el cooldown global; el riesgo es el volumen). |
| `cookies` | `add <ruta> [--keep-source]` | Importa cookies.txt/.json (cifra y guarda). El borrado del archivo fuente es best-effort por defecto (T14); `--keep-source` lo conserva (F-15). La salida informa del destino real del archivo (conservado/eliminado/NO eliminado). |
| `cookies` | `list` | Lista cookies con `validation_state`, motivo y countdown real de expiración. |
| `cookies` | `test <id>` | Prueba validez de una cookie (puede devolver `valid`/`invalid`/`inconclusive`). |
| `cookies` | `remove <id>` | Elimina cookie. |
| `videos` | `last [N]` | Últimos N vídeos descargados. |
| `videos` | `export [--format json\|csv]` | Exporta metadatos de vídeos descargados (CSV: `csv.writer` de la stdlib con quoting RFC 4180 + sanitización de prefijos `= + - @` y espacios/tabs iniciales contra inyección de fórmulas — trampas T49/F-11). **La exportación sale sin wrap ni markup de Rich** (`console.print(markup=False, soft_wrap=True)`): Rich envuelve a 80 columnas en no-TTY y corrompería JSON/CSV largos, e interpreta `[/]` y etiquetas desconocidas como markup (lección L-A6, §21). |
| `videos` | `integrity [username]` | Verifica integridad (tamaño + SHA-256 + ffprobe). |
| `system` | `disk` | Uso de disco, alertas y estado de `downloads_paused`; permite forzar la reanudación manual tras un ENOSPC limpiando el flag (`--resume`) (§4.3 punto 6, T45). |
| `system` | `backup` | Snapshot consistente en caliente de la base (API de backup de SQLite / `VACUUM INTO`) en `<DATA_DIR>/backups/` — **nunca** copiar el `.db` a pelo bajo WAL (capturaría un estado inconsistente). Implementado en el MVP (F-21b); mapea `sqlite3.OperationalError` a error limpio. Purga los snapshots más antiguos por encima de `SYSTEM_BACKUP_RETAIN_COUNT` (§23.3.6) *[Añadido en el análisis posterior]*. |

**Justificación de la organización en grupos**: comandos como `stats`, `disk`, `last`, `check`, `backup` o `export` no deben vivir sueltos fuera de un grupo ni romper el patrón grupo→subcomando. Agruparlos bajo `accounts`, `videos` y `system` según a qué operan da como resultado una CLI predecible donde cada comando sigue siempre la forma `tikdown-rs <grupo> <verbo> [args]`, sin excepciones.

**Paridad CLI ↔ Telegram**: el bot de Telegram sigue usando comandos planos (`/stats`, `/disk`, `/list`, etc. — ver §6.4) porque la sintaxis de Telegram no tiene un concepto nativo de subcomandos anidados; la paridad es funcional (misma función de `services/*` detrás), no textual.

**Implementación (trampa T18)**: `typer` no ofrece soporte async nativo — todos los comandos son wrappers síncronos que llaman `asyncio.run(...)`, centralizados en `cli/common.py` junto con `run_or_exit()` (convierte los errores de negocio — `AccountError`, `BackfillAccountError`, `ConfigurationError` — en `ERROR <mensaje>` + exit 1, sin tracebacks; F-21). Ese mismo módulo aplica las migraciones idempotentes pendientes (§5.5) y construye una `Settings` fresca por invocación (§5.6), antes de que el comando concreto abra su sesión.

**Regla de oro**: la lógica real vive en `services/*`. Los módulos de `cli/` y el dispatcher del bot **solo orquestan** llamadas a estas funciones. Nunca duplican lógica de negocio.

---

## 4. Motor de descarga: yt-dlp + curl-cffi

### 4.1 Selfcheck de impersonación antes de desplegar (crítico, especialmente en ARM64/aarch64)

El daemon **no arranca** si la impersonación TLS no está disponible. El mismo chequeo está disponible bajo demanda con `tikdown-rs daemon selfcheck`.

**Por qué es condición de existencia y no solo higiene (verificado en el fuente del extractor)**: el extractor de TikTok de yt-dlp fuerza `impersonate=True` en sus propias peticiones web (`_download_webpage_handle(..., impersonate=True)`, tanto en la extracción de vídeo como en la página de usuario). Es decir: sin curl-cffi operativo, la extracción de TikTok no se degrada — es imposible desde la primera petición. Corolario sobre la rotación de targets de §4.2: la rotación controlada por el engine cubre las peticiones que él inicia directamente; las internas del extractor usan el target que yt-dlp elija — la rotación sigue siendo útil, pero no debe asumirse que cubre el 100% del tráfico a TikTok.

**Contexto que el implementador debe conocer**: la disponibilidad de `curl_cffi` dentro de `yt-dlp` en plataformas `linux/aarch64` (Raspberry Pi, NAS ARM, Docker `arm64`) ha sido históricamente un problema recurrente en el ecosistema: es común que `--list-impersonate-targets` reporte todos los targets como `(unavailable)` incluso con `curl_cffi` instalado correctamente vía pip, típicamente al usar el **binario standalone** de `yt-dlp` en vez del paquete Python. Este proyecto evita ese vector al depender de `yt-dlp` como paquete Python dentro del propio venv/imagen (no el binario standalone), pero el selfcheck sigue siendo obligatorio porque el problema de detección puede reaparecer según la combinación exacta de versiones. Esto no es necesariamente un bug del proyecto — puede ser una limitación del ecosistema en esa arquitectura — el implementador debe verificar el estado actual antes de asumir una causa.

El selfcheck distingue explícitamente **tres causas posibles**, no solo "falló" (trampa T6):
1. `curl-cffi` ausente → mensaje: instalar `yt-dlp[default,curl-cffi]`.
2. Versión de `curl-cffi` no soportada por la nightly de yt-dlp pineada (serie incompatible, o una beta colada por un `prerelease` mal configurado) → mensaje: pinear la versión exacta compatible.
3. Targets vacíos pese a librería correcta → limitación de plataforma o cambio del API interno de yt-dlp → sugerir verificar `--list-impersonate-targets` directamente, probar imagen `amd64` por emulación, o considerar un host `amd64` (mini-PC) si el proyecto se va a operar en modo `monitor` de forma intensiva.

**El selfcheck completo incluye además una sonda de `ffmpeg`/`ffprobe`** (trampa T46): ambos binarios son dependencia dura (merge de formatos y verificación de integridad), y su ausencia hoy solo se descubriría en el primer merge fallido — clasificado además como error de descarga genérico. El selfcheck verifica que ambos son ejecutables y reporta versión.

Implementación recomendada del selfcheck (adaptar defensivamente al API interno exacto de la versión pineada de `yt-dlp`, que **no es una API pública estable** y puede cambiar entre versiones — envolver en `try/except` amplio y no asumir una forma de retorno fija; usar el logger del proyecto, no structlog — ver §1):

```python
import yt_dlp
import logging

log = logging.getLogger("tikdown_rs.verify")


def selfcheck_impersonation() -> None:
    ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
    available = []
    try:
        raw = getattr(ydl, "_get_available_impersonate_targets", lambda: [])()
        # La forma de retorno puede variar entre versiones de yt-dlp
        # (lista de targets, o lista de tuplas). Normalizar defensivamente
        # en lugar de asumir una forma fija.
        for item in raw:
            available.append(item)
    except Exception:
        log.warning("selfcheck.impersonation_api_changed", exc_info=True)

    if not available:
        log.critical(
            "curl-cffi / impersonacion TLS no disponible en esta plataforma. "
            "Verificar: (1) 'pip show curl_cffi', (2) version compatible con "
            "la nightly de yt-dlp pineada, (3) 'yt-dlp --list-impersonate-targets' "
            "directamente. Problema conocido en linux/aarch64."
        )
        raise SystemExit(1)

    log.info("Impersonacion TLS disponible", extra={"targets": len(available)})
```

**Los targets de impersonación son OBJETOS, no strings** (lección L-D1, §21): conservar los objetos `ImpersonateTarget` devueltos por `_get_available_impersonate_targets` y rotar sobre esos objetos. El CLI de yt-dlp parsea cadenas como `"chrome:133"` a objetos, pero el parámetro `params["impersonate"]` de la API Python **exige el objeto** — pasar strings revienta toda llamada real del motor con `AssertionError` en `is_supported_target` (el selfcheck pasa, porque solo cuenta targets; la primera descarga real fallaba). **Vigencia de esta afirmación (R11, §25.5)**: era cierta en la nightly pineada cuando se diagnosticó; hay evidencia de aceptación de strings en nightlies recientes (el parámetro se normaliza internamente antes de la validación). No es una verdad permanente: verificar el comportamiento contra la versión efectivamente pineada (§1.2). Conservar los objetos `ImpersonateTarget` sigue siendo compatible con ambas variantes, así que la regla de implementación no cambia.

**Punto más frágil del proyecto (documentar como tal, no como detalle menor)**: el selfcheck depende de `_get_available_impersonate_targets` y de `yt_dlp.networking.impersonate.ImpersonateTarget`, ninguno de los cuales forma parte de una API pública estable de `yt-dlp` — pueden renombrarse, cambiar de forma de retorno o desaparecer en cualquier release, incluida una nightly intermedia. Esto lo hace, en la práctica, más frágil ante actualizaciones que el propio extractor de TikTok (que al menos tiene cobertura de tests upstream). Implicaciones obligatorias para el diseño:
- **Flujo oficial de actualización de yt-dlp (definido, no implícito)**: actualizar = bump del pin en `pyproject.toml` + `uv lock` + rebuild/redeploy de la imagen. **No existe auto-actualización en caliente** dentro del daemon: el pin exacto y el build reproducible mandan. El **selfcheck completo se ejecuta en el primer arranque tras cada redeploy** (§5.1 paso 2) — una actualización "exitosa" que rompe silenciosamente el selfcheck es el escenario de fallo más peligroso, y el arranque es donde debe cortarse, no el primer intento de descarga.
- **Detección de regresión post-actualización**: el daemon persiste `last_known_good_ytdlp_version` en `daemon_state` (última versión que superó el selfcheck completo; F-14: se persiste en el arranque y en cada selfcheck periódico OK). Si en un arranque la versión instalada difiere de la última buena conocida **y** el selfcheck falla, se emite como alerta de máxima prioridad por Telegram (`daemon.selfcheck_broken_after_update`), distinta de la alerta genérica de impersonación no disponible: la causa probable es la versión recién aplicada, no el hardware. Tras un selfcheck exitoso se actualiza `last_known_good_ytdlp_version`.
- Mantener el `try/except` amplio mostrado arriba como única defensa estructural: nunca asumir que una forma de retorno concreta seguirá siendo válida en la siguiente versión pineada.

### 4.2 Reglas de uso y formato de descarga

- Toda llamada a `yt-dlp` (`extract_info`, listado de vídeos, perfil, validación de cookies) → `asyncio.to_thread(...)`, con timeout de 10 minutos por vídeo.
- **Nunca** usar `httpx`/`requests` contra dominios de TikTok.
- `httpx` solo para Bot API de Telegram y probe de red (HEAD a endpoint neutral, timeout 5s).
- Rotación round-robin de cookies válidas + targets de impersonación disponibles ante fallos.

**Formato de descarga recomendado** (evita pantalla negra / solo-audio en streams DASH, el fallo más común y menos obvio al integrar TikTok con yt-dlp):

```python
format_string = (
    "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[height<=1080]+bestaudio/"
    "best[height<=1080]/"
    "best"
)
merge_output_format = "mp4"
```

El `DownloadEngine` aplica este formato por defecto, con override global (`DOWNLOAD_FORMAT`) o por cuenta.

**Formato mejorado para el reintento ante fallo de solo-audio** (prioriza explícitamente pista de vídeo): `bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best`.

Nota sobre el formato: para TikTok la rama efectiva suele ser `best[height<=1080]`/`best` (formatos progresivos mp4); las ramas `bestvideo+bestaudio` cubren los casos DASH. Ambas deben permanecer.

**Opciones de endurecimiento del engine** (nombres verificados contra `yt_dlp` y el extractor de TikTok; valores de partida, ajustables en Settings):

| Opción yt-dlp | Valor recomendado | Motivo |
|---|---|---|
| `extractor_retries` | 5-10 para listados de feed | Los tests upstream de `TikTokUserIE` usan `extractor_retries: 10` por la inestabilidad conocida del feed |
| `sleep_interval_requests` | 1-3 s | Espacia las peticiones **dentro** de una extracción multi-página (el cooldown global de §4.5 no las cubre — trampa T56); el propio código WAF del extractor lee este parámetro |
| `socket_timeout` | 20-30 s | Acota hilos colgados en conexiones muertas; complementa (no sustituye) el timeout de 10 min por vídeo de §15 |
| `fragment_retries` | 10 (default upstream) | No tocar sin motivo |

### 4.3 Clasificación de errores (orden de evaluación obligatorio — trampa T5)

`classify_error` evalúa **en este orden**, no en cualquier orden:

El matching es **case-insensitive sobre la cadena completa de la excepción** (incluida la causa encadenada), nunca sobre subcadenas cortas aisladas. Los marcadores de autenticación y bloqueo de esta sección están **verificados contra los mensajes literales que emite el extractor de TikTok** de yt-dlp (trampa T52).

1. Marcadores de bloqueo por IP/ritmo (`blocked`, `ip address is blocked`, etc.) → **transitorio**. Debe evaluarse **antes** que el punto 3 (403 genérico), porque este tipo de bloqueo suele venir envuelto en un 403 y sin esta prioridad se clasificaría mal. *(Verificado: el extractor emite literalmente `Your IP address is blocked from accessing this post` ante el `status 10204`.)*
2. Marcadores de autenticación → **definitivo**. Lista verificada (trampa T52): `requiring login`, `login required`, `log into an account`, `log in for access`, `permission to view`, `account is private`, `captcha`, `banned`, `suspended`, `session expired`. *(Los mensajes reales del extractor —`TikTok is requiring login for access to this content`, `You do not have permission to view this post. Log into an account that has access`, `This post may not be comfortable for some audiences. Log in for access`— **no** contienen la subcadena `login required`; una lista basada solo en ese literal clasificaría fallos de auth como transitorios.)*
3. **Un 403 genérico sin evidencia de autenticación → transitorio** (solo se marca definitivo si el mensaje contiene alguno de los hints de auth del punto 2). Confundir este caso es el error de diseño más común: la cookie no es el problema, así que invalidarla no soluciona nada y además detiene backfill/monitor innecesariamente.
4. Contenido inexistente (`video unavailable`, `404`, `removed`, `status code` **distinto de 0** y desconocido, ...) → **definitivo**. **Excepción verificada (trampa T53)**: `Video not available, status code 0` → **transitorio** — el `status 0` es el fallback del extractor cuando TikTok devuelve una página sin `webapp.video-detail`, la firma exacta de una respuesta degradada/anti-bot, no de contenido inexistente. Si persiste tras agotar los reintentos rotando cookie/target → definitivo por agotamiento, nunca por el primer mensaje.
5. Degradación del extractor/feed → **transitorio**: `keeps sending the same page` (el propio yt-dlp lo marca `expected` y reintenta con otro `device_id` — trampa T54), `unable to extract`, `no entries`, JSON inválido.
6. Errores locales de disco (`no space left`, ENOSPC) → **fallo local accionable** (ni definitivo ni transitorio de TikTok — trampa T45): pausar las rutas de descarga poniendo `downloads_paused=1` en `daemon_state` (el motor lo consulta junto a `network_available.wait()` antes de **cada** intento, §9) y emitir alerta por Telegram; no cuenta para el circuit breaker ni toca cookies. La reanudación es **automática** cuando el job de chequeo de disco (§5.1) detecta espacio libre de nuevo por encima del umbral (con notificación), y también puede forzarse manualmente desde `system disk --resume` (§3).
7. Caso informativo, no-error: `does not have any videos posted` → la cuenta simplemente no tiene contenido. No cuenta para el circuit breaker ni marca `needs_review`; registrar como estado de la cuenta.
8. Transitorios genéricos (429, timeouts, `service unavailable`, ...) → **transitorio**.
9. Por defecto → **transitorio** (nunca asumir definitivo por defecto).

**Evitar marcadores demasiado amplios** (`account`, `not available`) que convierten silenciosamente errores transitorios en definitivos.

### 4.4 Resiliencia, backoff y circuit breaker

**Pacing vs reacción (distinción de diseño, no confundir)**: el *cooldown* (§4.5) es espaciado **aleatorio por descarga** entre MIN y MAX (30–120s por defecto — no crece con los fallos); el *backoff* es **reacción** a fallos (exponencial con techo). Son dos mecanismos independientes que nunca se sustituyen: el cooldown actúa siempre antes de cada intento, el backoff se suma solo tras un fallo.

- Backoff exponencial con jitter (techo 30 min) ante transitorios genéricos.
- **Backoff específico y configurable para el muro anti-bot de TikTok** (respuesta inesperada de la petición web mientras la cookie sigue siendo válida — esto es bloqueo por IP/ritmo, no por credenciales): clasificar siempre como transitorio, reintentar con backoff exponencial empezando en `YTDLP_ANTIBOT_BACKOFF_BASE_SECONDS` (default 10s), duplicando en cada intento hasta un techo configurable `YTDLP_ANTIBOT_BACKOFF_CEILING_SECONDS` (default 120s).
- Circuit breaker por cuenta: 5 fallos de **auth** consecutivos → `paused + needs_review`. Los fallos transitorios (anti-bot, red, extractor puntual) **no** cuentan para este contador — solo fallos de autenticación reales. El registro del breaker vive en memoria — del proceso que ejecuta la ruta de descarga (el daemon, o la CLI en un backfill foreground) — y **se resetea en cada reinicio**; limitación documentada: las pausas de cuenta sí persisten en base de datos, los conteos parciales no. El disparo del breaker emite `monitor.account_paused` (F-08).
- **Techo de reintentos transitorios por vídeo (`MAX_VIDEO_RETRY_COUNT`, default 5)**: un fallo transitorio se reintenta con backoff, pero no indefinidamente — al alcanzar el techo, el vídeo pasa a `status='failed'` con `error_category='transient'` y se emite `download.retry_exhausted` (trampa T58: un transitorio persistente sin techo es un reintento infinito invisible). El vídeo queda recuperable vía `backfill retry-failed`.
- **Presupuesto de tiempo total por vídeo (`MAX_VIDEO_TOTAL_TIME_SECONDS`, default 900s — trampa T63)**: los reintentos son inline y el semáforo global es 1, así que sin un presupuesto agregado un solo vídeo (5 intentos × 10 min de timeout + backoffs de hasta 30 min) puede bloquear **todas** las descargas durante más de una hora (head-of-line blocking). Agotado el presupuesto de tiempo o el de reintentos — lo primero que ocurra —, el vídeo pasa a `failed`/`transient` + `download.retry_exhausted`.
- **Los fallos de red no consumen reintentos (trampa T64)**: un fallo clasificado como red (probe `offline` confirmado o `network_available` en clear, §9) no incrementa `retry_count` ni agota `MAX_VIDEO_RETRY_COUNT`/`MAX_VIDEO_TOTAL_TIME_SECONDS` — invalidar trabajo por una caída ajena al vídeo es el mismo anti-patrón que invalidar una cookie sana por falta de red (§9). El vídeo se reintenta al volver la red sin penalización.
- Health checks proactivos del monitor sobre accesibilidad de cuentas → eventos `monitor.account_unreachable` / `monitor.account_health_check_failed` (este último emitido por el job de refresco de perfil, F-08).
- Diferenciar errores de red (probe falla → no cuenta para el circuit breaker) de errores específicos de TikTok (403/429 por cuenta → sí cuenta).

### 4.5 Concurrencia, cooldown global y rutas

- **Semáforo global de descargas** (`MAX_CONCURRENT_DOWNLOADS`, default 1) a **nivel de proceso**, no por instancia de `DownloadEngine`.
- **Cooldown global entre descargas, en un único punto de paso compartido por todas las rutas y todos los procesos**: monitor, backfill (daemon o CLI), `retry-failed` y descargas manuales del bot comparten el mismo mecanismo de espaciado, con un reloj compartido **persistido en SQLite** (tabla singleton `download_pacing_state`, §2), no solo en memoria del proceso. Motivo: daemon y CLI son procesos distintos que descargan desde la misma IP — un pacing solo intra-proceso permite que un `backfill run` por CLI en paralelo al monitor del daemon duplique la tasa de peticiones a TikTok sin que ninguno de los dos cooldowns lo vea, exactamente el patrón que este diseño existe para evitar. **El espaciado es un sorteo aleatorio uniforme por descarga en [`GLOBAL_DOWNLOAD_COOLDOWN_MIN_SECONDS`, `GLOBAL_DOWNLOAD_COOLDOWN_MAX_SECONDS`] (defaults 30s/120s; `MIN=MAX` → fijo; ambos `0` → desactivado; `MAX<MIN` → error de configuración en `validate_for_daemon()`, T25)**: un intervalo fijo produce un patrón de peticiones en metrónomo, trivialmente fingerprinteable por el anti-bot de TikTok (trampa T62); el jitter aleatorio rompe esa regularidad sin renunciar al pacing. Esto es intencional desde el diseño, no un parche: si cada ruta implementa su propio pacing por separado, es fácil que dos rutas activas a la vez (p. ej. monitor + reintento manual) generen ráfagas de peticiones que disparan el limitador anti-bot de TikTok aunque cada ruta individualmente respete su propia pausa. **Trampa T22**: el `reserve()` del cooldown debe ser **atómico y cross-proceso** — implementado como `UPDATE download_pacing_state SET next_allowed_at = ... RETURNING next_allowed_at` (SQLite ≥3.35) en una única operación, de modo que sortear la duración y marcar el inicio del siguiente hueco ocurra atómicamente y sea visible para todos los procesos; cada proceso espera localmente hasta la hora reservada antes de iniciar su intento. Un cooldown por instancia de engine o solo en memoria del proceso permite que, por ejemplo, el bot y el monitor — o la CLI y el daemon — descarguen en paralelo sin pacing común. El cooldown vive en la capa compartida de descargas (`core/download_engine.py` o un módulo dedicado), nunca duplicado en `services/monitor.py` y `services/backfill.py` por separado. Se aplica antes de **cada** intento, incluidos los reintentos; tras un fallo, el backoff de §4.4 se suma al cooldown, no lo sustituye. El RNG del sorteo es inyectable para tests (F.I.R.S.T., §14). **Dos detalles críticos verificados en la implementación anterior (§21, L-C6/L-C7)**: el `INSERT ... ON CONFLICT DO NOTHING` de la fila singleton debe llevar `commit` inmediato (sin él, la fila nunca persiste y el CAS falla siempre → fail-open silencioso), y los timestamps de pacing usan precisión de milisegundos (con segundos, dos reservas rápidas colapsan al mismo valor y el CAS pierde la distinción).
- **Rutas derivadas de `DATA_DIR` (trampa T8, bloqueante)**: `videos_root = <DATA_DIR>/videos`; el `outtmpl` por defecto es `<DATA_DIR>/videos/%(uploader)s/%(id)s.%(ext)s`. Jamás rutas relativas al directorio de trabajo: en Docker quedan fuera del `VOLUME` (vídeos perdidos al recrear el contenedor) y con rutas relativas fallan por permisos. Un módulo `core/paths.py` centraliza `videos_root(data_dir)` y `default_outtmpl(data_dir)`; los servicios reciben la raíz por parámetro, nunca la construyen ad-hoc.
- **`--download-archive` real en el motor**: monitor, backfill y retry pasan la ruta del archive a `engine.download()` (`archive_path=str(archive.path)` en **ambas** llamadas del embudo — intento normal y reintento con formato mejorado; F-02). El `DownloadArchive` local complementa: `add()` tolerante a duplicados físicos (si yt-dlp ya escribió la línea, no duplica), `discard()` con reescritura atómica. **El parser del archive debe reconocer ambos formatos de línea** (lección L-C8, §21): yt-dlp escribe `tiktok <id>` (extractor + id) mientras que el archivo propio puede guardar el id pelado — el ID es el **último token** de la línea; `contains()`/`discard()` deben entender ambos formatos o la deduplicación app-level falla en silencio.
- **Método de escritura del archivo de deduplicación**: no usar un patrón de "escribir a temporal + `os.replace()`" por cada línea nueva. En su lugar: abrir en modo append, escribir una única línea completa por llamada a `write()` y hacer `flush()` + `os.fsync()` tras cada escritura — POSIX garantiza que un `write()` de una línea corta es atómico a nivel de sistema operativo. El parser del archive debe además **tolerar y saltar una última línea malformada** (trampa T47: un corte de energía a mitad de `write()` puede dejar una línea parcial al final del archivo — la tabla SQLite sigue siendo la fuente de verdad).
- **Timeout del hilo nativo de yt-dlp (limitación documentada, trampa T23)**: `asyncio.wait_for(asyncio.to_thread(...), timeout)` cancela el `Future` de asyncio pero **no** el hilo nativo de yt-dlp en ejecución. El hilo termina solo en background; el archivo parcial que pueda quedar se descarta por la verificación de integridad (§4.6) y el descarte del archive permite el reintento posterior. Dos efectos residuales de tratamiento obligatorio: (1) el hilo zombi **sigue haciendo peticiones a TikTok** fuera del cooldown mientras viva — el motor registra los hilos zombis activos y los expone en `daemon status` como diagnóstico (no pueden matarse de forma segura); (2) el zombi puede seguir escribiendo el **mismo** `outtmpl` que el reintento posterior (trampa T66) — todo reintento tras timeout escribe a una ruta temporal distinta (sufijo `.retry-N`) que solo se renombra a la definitiva tras superar la verificación de integridad, para que un zombi tardío nunca corrompa un archivo ya verificado. `MAX_VIDEO_TOTAL_TIME_SECONDS` (§4.4) acota cuántos zombis pueden acumularse. Documentar esta limitación explícitamente en el README.

### 4.6 Verificación de integridad post-descarga

Centralizada en un único helper (p. ej. `services/videos.handle_download_result`), usado por monitor, backfill **y** `retry-failed` — nunca lógica de integridad duplicada por ruta:

1. El archivo existe y tiene tamaño > 0. Si no → fallo categorizado `error_category='integrity'` explícito y accionable, **nunca** un error crudo sin clasificar y **nunca** marcado como `downloaded`. Un resultado "sin resultado / nada que descargar" convertido en un error genérico no clasificado es la causa más probable de que `retry-failed` parezca "no hacer nada" sin dar pista de por qué.
2. Se calcula SHA-256 y se guarda en `file_hash`.
3. `ffprobe` valida pista de vídeo, duración > 0, codecs y resolución detectables.

**Un archivo sin pista de vídeo tiene DOS causas posibles, y solo una es un fallo (trampa T55 — verificado en el fuente del extractor y en sus tests)**:

1. **Slideshow / post de fotos (resultado esperado, no fallo)**: para estos posts el extractor solo ofrece formatos con `vcodec='none'` (p. ej. `format_id='audio'`) — el audio de la música es todo lo que existe descargable, y el propio extractor documenta que estos posts tienen duración de vídeo 0 y duración real de audio. Marcarlos como fallo de integridad los reintentaría en bucle para siempre.
2. **Respuesta degradada (fallo real)**: un vídeo normal donde TikTok solo expuso el formato de audio en esa respuesta (caso documentado upstream: *"only audio available via web"*). Aquí sí aplica el reintento con formato mejorado.

**Distinción operacional**: el engine conoce el `info_dict` en el momento de la descarga — `handle_download_result` recibe además un resumen de la extracción (`expected_has_video: bool`, verdadero si la extracción ofrecía al menos un formato con pista de vídeo). Si `expected_has_video` es falso → el post se registra con `status='skipped'` (§2) y se archiva su ID en la deduplicación para no reprocesarlo, **sin** reintentos y **sin** contar como fallo. Si es verdadero → flujo de reintento siguiente.

**Reintento automático ante fallo de "solo audio" degradado**: si la verificación detecta que el archivo descargado no tiene pista de vídeo **habiendo sido esperada** (`expected_has_video=True`), el flujo es:
1. Limpiar los archivos parciales.
2. **Descartar la entrada del archive de deduplicación ANTES del reintento** (trampa T24: yt-dlp pudo marcar el `tiktok_video_id` como archivado ya en el primer intento; sin este descarte previo, el segundo intento vería el ID ya archivado y devolvería "already downloaded" sin llegar a probar el formato mejorado).
3. Reintentar **una vez** con el formato de descarga mejorado (§4.2).
4. Si persiste, fallo limpio con `error_category='integrity'`.

Este disparo debe activarse específicamente desde el resultado de la verificación de integridad, no desde una categoría genérica de error de descarga: un fallo de integridad post-descarga es una señal distinta de un fallo de red o de auth, y necesita su propia rama de reintento. El reintento con formato mejorado emite `download.format_retry` (F-08).

`handle_download_result` acepta un `base_retry_count` (el reintento acumula sobre el contador previo del vídeo en base de datos, no solo sobre los reintentos internos de esta llamada concreta) **y el resumen de extracción con `expected_has_video`** descrito arriba — ambos son parte obligatoria de su firma en las tres rutas (monitor, backfill, `retry-failed`).

**El canal de eventos de `handle_download_result` es SÍNCRONO** (lección L-G2, §21): el handle invoca `on_event(...)` **sin** `await`. Un llamador que envuelva el canal en un `async def` crea una corrutina que nunca se ejecuta y pierde todos los eventos en silencio (en la implementación anterior, el monitor perdía así todos los `download.completed`). Si un componente necesita un wrapper async para sus propios eventos, lo usa solo con `await`; el canal que se propaga a `handle_download_result` es el síncrono original.

**`notify_on_download` se propaga desde la cuenta en TODAS las rutas** (lección L-G3, §21): monitor, backfill y `retry-failed` llaman a `handle_download_result` con `notify_on_download=bool(account.notify_on_download)` — si solo lo propaga el monitor, los `download.completed` del backfill nunca llegan pese a tener la cuenta con notificación activada.

I/O pesado (hashing, `ffprobe`, globs de localización de archivos) → `asyncio.to_thread` (trampa T12: bloquear el event loop con estas operaciones "cortas" es fácil de pasar por alto porque no son tan obviamente bloqueantes como una llamada de red).

### 4.7 Extensibilidad del motor (no acoplar todo a yt-dlp)

- Definir `DownloadEngine` como `typing.Protocol` con métodos `download`, `extract_profile`, `list_videos`, `validate_cookie`.
- `services/*` **nunca** importa `yt_dlp` directamente, solo la interfaz.
- Esto permite en el futuro añadir un motor secundario/fallback sin tocar la lógica de negocio. Actualmente, yt-dlp + curl-cffi es el único motor.
- **Soporte de proxy, desactivado por defecto**: `YTDLP_PROXY_URL` acepta una URL única o una lista separada por comas. **La rotación round-robin real entre varios proxies está implementada en el MVP** (decisión F-21b: el punto de backlog histórico se cerró al confirmarse la implementación); el parámetro se parsea en el engine y rota entre los proxies configurados.

### 4.8 Trampa conocida al usar `rich.progress` con datos propios (T3)

Al construir la barra de progreso del backfill, `rich.progress.Progress` reserva ciertos nombres de campo (por ejemplo `total`/`completed`) para su propio uso interno en `update()`. Si el diccionario de datos de negocio usa alguno de esos nombres reservados para un campo propio distinto (p. ej. un contador de "vídeos totales" con el mismo nombre), la mutación afecta a la barra real en vez de solo mostrarse, y si además se accede a un campo propio con la sintaxis de atributo (`{task.fields.clave}` en vez de `{task.fields[clave]}`), el render falla con un error en el primer refresco — no en la construcción del objeto, lo cual lo hace fácil de pasar por alto en pruebas superficiales que solo instancian el objeto sin ejecutar un render real. **Mitigación**: usar nombres de campo propios que no colisionen con los reservados de la librería (p. ej. `procesados`, `correctos`, `fallidos`, `esperados`), referenciarlos siempre con `{task.fields[clave]}`, y cubrir el render con un test de CLI que ejecute con datos simulados completos (no solo que construya el objeto) — ver §14.

### 4.9 Listado de feeds de usuario (mecánica compartida del monitor y del backfill)

Verificado contra `TikTokUserIE` del extractor: el feed de usuario se pagina vía `api/creator/item_list` en páginas de 15 ítems, **de más nuevo a más viejo**, con cursor interno = `createTime` (ms) del último ítem de la página, hasta agotar `hasMorePrevious`. Dos propiedades del diseño se apoyan directamente en este comportamiento:

- **Las entradas del listado ya traen metadatos completos** (`createTime` → `upload_date`, título, duración, thumbnails, stats) — no hace falta re-extraer cada vídeo para fecharlo. El `upload_date` se conserva en el formato canónico que entrega yt-dlp (`YYYYMMDD`) en toda la cadena (trampa T43: mezclar `YYYYMMDD` con ISO8601 en la misma columna rompe la comparación lexicográfica del cursor). El cursor por `upload_date` de §10 es viable tal cual, y la detección de vídeos nuevos del monitor es una **diferencia de conjuntos** entre los IDs listados y la tabla `download_archive`, nunca una re-descarga de metadatos.
- **Coste por comprobación: ~2 peticiones** (página de usuario impersonada para resolver `sec_uid` + una página de feed). Comprobar una cuenta en modo `monitor` es barato; el volumen agregado lo fijan el throttle de 30s por cuenta y el intervalo de §15. **El ciclo del monitor respeta ese throttle**: una cuenta con `last_check_at` < 30s (p. ej. recién comprobada con `accounts check` manual) se salta en esa iteración — el ciclo nunca duplica requests sobre una cuenta recién comprobada por otra vía. **Ojo (lección L-G1, §21)**: el throttle debe distinguir "recién comprobada" de "nunca comprobada" — una cuenta con `last_check_at=NULL` se comprueba siempre; tratar `NULL` como 0 segundos hacía que las cuentas recién añadidas no se comprobaran jamás.
- **`upload_date` ausente (defensivo)**: si una entrada del feed carece de `createTime` (respuesta degradada parcial), se le asigna como `upload_date` el valor del cursor anterior de esa cuenta en vez de NULL — un NULL rompería la comparación lexicográfica del cursor de backfill (§10) y dejaría al vídeo fuera del alcance de reanudación. El cursor local debe **actualizarse tras cada vídeo procesado** para que este fallback no use un valor stale (lección L-F2, §21).

Detección de estados de cuenta en el propio listado (clasificar según §4.3): cuenta privada (`statusCode 10222`), cuenta sin vídeos (`does not have any videos posted` — informativo), feed que repite página (`keeps sending the same page` — transitorio, T54).

---

## 5. El daemon: arranque, scheduler y apagado

### 5.1 Arranque (`tikdown-rs daemon run`) — orden estricto

1. `settings.validate_for_daemon()` — fail-fast de configuración (trampa T25: sin este paso, una configuración inválida podía dejar el daemon arrancado a medias, con jobs registrados apuntando a recursos incompletos).
2. Selfcheck de impersonación (fail-fast, §4.1).
3. Aplicar migraciones Alembic pendientes (idempotentes, §5.5) — ejecutadas en `asyncio.to_thread` (el `env.py` de Alembic hace `asyncio.run()` internamente; llamarlo dentro del loop del daemon revienta — lección L-B3, §21).
4. **Reaplicar el setup de logging inmediatamente después de las migraciones** (trampa T72): `fileConfig()` de Alembic reconfigura el ROOT logger (nivel `WARNING` + handler stderr de `alembic.ini`); `disable_existing_loggers=False` no basta. Reaplicar `_setup_logging(settings.log_level)` con `basicConfig(force=True)` — sin esto, `docker logs` queda con 0 bytes y el daemon parece mudo (lección L-J3, §21).
5. Cargar/generar `FERNET_KEY` (jerarquía: env var → `<DATA_DIR>/fernet.key` (permisos 0600, corrigiendo si una clave existente tiene permisos más amplios — trampa T7) → generar nueva). La generación es **atómica** (`open(..., 'xb')`/`O_EXCL` — trampa T67): si dos procesos (daemon + CLI en un primer arranque simultáneo) intentan generarla a la vez, el perdedor relee la existente — sin esto, el último en escribir gana y deja las cookies cifradas con la otra clave permanentemente irrecuperables. **Ambas ramas de carga toleran el archivo vacío** (ventana entre el `O_EXCL` del ganador y su escritura): clave leída vacía → reintentar la lectura (50×10ms) y solo propagar corrupción no vacía o vacío persistente (lección L-E2, §21).
6. Construir componentes: `NetworkMonitor`, `DownloadEngine` (con el evento de red inyectado y los coordinadores globales de semáforo/cooldown), `DownloadArchive`, session factory.
7. Estado inicial en `daemon_state`: el monitor **siempre arranca detenido** — el usuario debe ejecutar `tikdown-rs monitor start` manualmente tras cada reinicio del daemon (decisión de seguridad intencional, controlable vía `MONITOR_AUTOSTART` en §12). Acto seguido, **reconciliaciones de estado**: (a) transiciones pendientes history→monitor (§10, trampa T59): aplicar la `UPDATE` idempotente a toda cuenta con `monitor_after_backfill=1 AND backfill_status='completed'`; (b) backfills huérfanos: `reconcile_stale_backfills()` devuelve a `queued` todo backfill que quedó en `backfilling` tras crash o apagado (F-10).
8. Bot de Telegram (si el modo incluye `commands`/`both`), con **dependencias inyectadas por el daemon** (engine, motor, archive, clave — trampa T26: el bot nunca debe crear sus propios engines por comando) y el **canal de eventos inyectado** (F-08: sin él, `bot.unauthorized_attempt` no tendría emisor).
9. Registrar jobs del scheduler (jobstore en memoria, §5.4): ciclo del monitor (solo si `monitor_running=1`), validación de cookies (6h), refresco de perfil (48h), probe de red, heartbeat (`HEARTBEAT_INTERVAL_SECONDS`, default 10s), comprobación de nueva versión de yt-dlp (24h), chequeo de disco (15-30 min, umbral `DISK_WARNING_FREE_PERCENT` — productor de `monitor.disk_warning` y de la reanudación automática tras ENOSPC, trampas T45/T65), selfcheck periódico (24h, resultado persistido en `daemon_state.last_selfcheck_ok`; en cada OK se actualiza `last_known_good_ytdlp_version`, F-14) y recogida de backfills `queued` (§10). Todos los jobs de ciclo potencialmente largo se registran con `max_instances=1` y `coalesce=True` (trampa T44: un ciclo de monitor más lento que su intervalo no debe solaparse consigo mismo). **El heartbeat actúa además como watcher de control**: observa `daemon_state.monitor_running` y registra/elimina el job del ciclo del monitor en caliente cuando un `monitor start/stop` desde CLI cambia el flag — mismo patrón que la detección de `stop_requested` (§5.2). El job de recogida de backfills comprueba `backfill_slot_busy()` antes de crear la tarea (F-10) y **propaga el canal de eventos a la corrutina que lanza** (trampa T75: un backfill lanzado sin canal emite todos sus eventos a `None` — lección L-I5, §21).
10. Bloquear el arranque del monitor si no hay cookies válidas.
11. Loop principal: `await stop_event.wait()` — sin polling activo.

**Un único event loop para todo el ciclo de vida (lección L-B1, §21 — crítica)**: `start()`, `run()` y `shutdown()` del daemon corren dentro de **UN único `asyncio.run(_lifecycle())`**. La implementación anterior usaba un `asyncio.run()` por fase: cada llamada crea un event loop NUEVO, el scheduler y los jobs quedaban atados al loop de `start()` y, al cerrarse ese loop, el heartbeat dejaba de ejecutarse y el watcher de `stop_requested` nunca disparaba — resultado: `daemon stop` "funcionaba" sin error visible pero el proceso seguía vivo como zombi.

### 5.2 Apagado limpio (SIGTERM, SIGINT o `tikdown-rs daemon stop`) — orden crítico

1. `stop_event` se activa (por signal handler, o por `stop_requested` detectado en el propio heartbeat cuando el apagado se pidió vía CLI).
2. **Detener el scheduling y drenar el trabajo en curso (~10s)** — en ese orden: primero `scheduler.pause()`/`shutdown()` como **señal** para que no se encolen nuevos jobs, y a continuación el drenaje real (paso 6: espera con timeout + cancelación explícita del registro de tareas supervisadas). **Trampa T9 (verificada en el fuente de APScheduler 3.x)**: con `AsyncIOScheduler`, `shutdown(wait=True)` **no espera** a los jobs en curso — `AsyncIOExecutor` cancela los futures pendientes (su propio código lo admite: *"There is no way to honor wait=True without converting this method into a coroutine method"*) y, llamado desde dentro del propio event loop, el cierre se programa con `call_soon_threadsafe` y retorna inmediatamente. Confiar el drenaje a `wait=True` es creer que los jobs terminaron cuando en realidad fueron cancelados o el cierre aún no se ha procesado. Por eso los jobs de ciclo largo (monitor, validación de cookies, backfill) lanzan su trabajo como **tareas supervisadas** (§5.7): ese registro es el único mecanismo que realmente espera/cancela trabajo en curso antes de disponer recursos compartidos (T27/T28). La misma disciplina es válida para la serie 4 de APScheduler (su `stop()` cancela el cancel scope del task group).
3. **Emitir `daemon.stopped` ANTES del drenaje** (F-19): la tarea de envío entra en `cancel_pending` (10s) y el spool aún tiene el engine vivo. Emitirlo después de disponer recursos hacía que casi nunca se entregara.
4. Detener el bot (`updater.stop() → stop() → shutdown()`).
5. Señal de parada al motor de descargas (`DownloadEngine.request_stop()`), que corta descargas entre fragmentos; máximo 10s para que las descargas en curso terminen.
6. Descargas pendientes: se marcan `cancelled` y se reintentan al reiniciar — **con productor definido (§10)**: `cancelled` no es estado terminal para el cursor de backfill ni entra en el archive de deduplicación; el backfill interrumpido vuelve a `queued` vía `except asyncio.CancelledError` (F-10) y la reconciliación de arranque (§5.1 paso 7, `reconcile_stale_backfills()`) lo relanza, de modo que la pasada reanudada redescubre esos vídeos por el feed y los reintenta. En cuentas en modo `monitor`, la diferencia de conjuntos del ciclo contra `download_archive` produce el mismo rescate. `retry-failed` no los recoge: no son fallos, ya tienen productor.
7. Drenaje (10s) y **cancelación explícita** de las tareas supervisadas pendientes (trampa T28: el registro de tareas debe guardar referencias `Task` reales, no solo nombres o contadores, para poder cancelarlas de verdad — un registro que solo cuenta no permite cancelar nada).
8. Limpieza de `daemon_state` (`daemon_pid`, `last_heartbeat_at`, `monitor_running=0`) y dispose del engine de base de datos.

Registrar `SIGTERM` y `SIGINT`. En Windows solo `SIGINT` está disponible vía `add_signal_handler`; aceptable porque el entorno de producción objetivo es Linux/Docker — Windows es solo para desarrollo local, y esto debe quedar documentado explícitamente.

### 5.3 Auto-detención por falta de cookies

Si la última cookie válida expira, el monitor se detiene globalmente y emite `monitor.stopped_no_cookies` con notificación accionable por Telegram. Espejo en el backfill: si no hay ninguna working cookie al iniciar, el backfill aborta con el evento `backfill.no_cookies` (F-01).

### 5.4 Elección de jobstore del scheduler (decisión de diseño, no un parche posterior)

**Usar jobstore en memoria (`MemoryJobStore`, el default de `AsyncIOScheduler`) desde el diseño inicial**, no un `SQLAlchemyJobStore` persistido en disco. Motivo técnico a tener en cuenta desde el primer commit: `SQLAlchemyJobStore` serializa los jobs con `pickle`; si los jobs se registran como *bound methods* de una clase que a su vez referencia al propio scheduler (patrón común y natural en un daemon orientado a objetos), la serialización falla.

- Todos los jobs de este daemon son de intervalo simple, recreados de forma determinista en cada arranque (§5.1, paso 9). No hay ningún caso de uso real en que se necesite que un job "recuerde" su próxima ejecución tras un reinicio del proceso.
- El único estado que sí debe sobrevivir a un reinicio (cursores de backfill, `daemon_state`, offset de Telegram) ya vive en SQLite de negocio, gestionado explícitamente por `services/*`, no por el jobstore del scheduler.
- Conclusión: jobstore en memoria es la opción correcta y más simple para este proyecto.
- **Nota de seguridad (verificada 2026-08)**: CVE-2026-31072 documenta una deserialización insegura (RCE potencial) en los serializadores JSON/CBOR de APScheduler, explotable cuando una aplicación deserializa entrada no confiable a través de ellos. Al usar `MemoryJobStore` — sin serializadores, sin jobstore persistente y sin endpoints que procesen entrada externa — ese vector no aplica a este proyecto. Es un argumento más para mantener esta decisión: no introducir un jobstore persistente ni serialización de jobs sin una revisión de seguridad explícita (§22 prevalece).

### 5.5 Migraciones idempotentes (requisito desde el día uno)

Tanto el arranque del daemon como cada comando CLI ejecutan las migraciones Alembic pendientes antes de abrir sesión. Debe soportarse explícitamente el caso en que existan tablas de aplicación pero **no** exista la tabla `alembic_version` (por ejemplo, tras una creación manual de esquema o un arranque parcial previo). La lógica de migración debe:
- **Comprobar primero si existe `alembic_version`** (trampa T29: una lógica que solo comprueba "existen tablas de negocio → stamp" sin verificar antes si `alembic_version` ya existe, repite el `stamp` en cada comando en vez de aplicar `upgrade` normalmente).
- Si existen tablas de negocio pero no `alembic_version` → ejecutar `command.stamp(alembic_cfg, "head")`.
- En cualquier otro caso → ejecutar `command.upgrade(alembic_cfg, "head")` normalmente.
- **Lock de migración entre procesos (trampa T68)**: toda la secuencia anterior se ejecuta bajo un lock de fichero en `DATA_DIR` (p. ej. `<DATA_DIR>/.migrate.lock`, con `fcntl`/`msvcrt` según plataforma). Dos procesos (daemon + CLI, o dos CLIs) migrando a la vez compiten sobre `alembic_version` y las DDL — el primero migra, el segundo espera y al entrar encuentra el esquema ya al día.
- **Exención de los comandos de sondeo (R10, §25.5)**: `daemon healthcheck` y el callback global `--version` **no ejecutan migraciones ni adquieren `.migrate.lock`**. El `HEALTHCHECK` de Docker invoca el healthcheck en cada intervalo (~30 s): migrar ahí añade churn del lock contra el daemon y superficie de falsos negativos bajo contención, sin beneficio — es el arranque del daemon (§5.1) y los comandos de negocio quienes mantienen el esquema al día. El healthcheck abre la base en modo lectura y, si el esquema está ausente o es incompatible, reporta unhealthy (exit 1) en vez de migrar. Esto formaliza además lo que §14 y T70 ya asumían: el smoke `--version` no migra.
- **Localización de `alembic.ini` y `alembic/` (trampa T70)**: resolver por candidatos con error explícito — (1) junto al módulo (`core/migrations.py`, dev editable), (2) cwd (el Dockerfile hace `COPY . .` + `WORKDIR /app` → `/app/alembic.ini` existe en la imagen publicable). Si ninguno existe, `FileNotFoundError` accionable en vez del error confuso de Alembic (`No 'script_location' key found`). **Nunca** asumir `Path(__file__).resolve().parents[1]`: con `uv sync --no-editable` el módulo vive en site-packages y el wheel no empaqueta los archivos de la raíz (lección L-J4, §21).
- `alembic/env.py` con **template async** (`connection.run_sync`) — el default síncrono no sirve con aiosqlite (trampa T51).
- Nivel del logger `alembic` a `WARNING` en `alembic.ini` (las migraciones se ejecutan en cada invocación CLI; el INFO ruidoso ensucia la salida humana — lección L-J1, §21).

### 5.6 Configuración de base de datos siempre desde el objeto propio, nunca desde config global cacheada

El daemon (y cualquier componente que abra su propia conexión: el bot, un comando CLI, un test) debe construir su conexión a base de datos a partir de **su propia configuración recibida explícitamente**, nunca de un singleton de configuración global cacheado a nivel de módulo. Depender de una configuración global implícita hace que sea fácil, sin darse cuenta, apuntar a la base de datos por defecto del proyecto en un contexto donde se esperaba una base de datos aislada (por ejemplo en tests) — y además ensucia el árbol del proyecto con archivos que no deberían existir fuera de despliegue real. En tests: `Settings(_env_file=None, data_dir=tmp)` + verificación explícita de que el directorio de datos por defecto **nunca** se crea durante la ejecución de la suite.

### 5.7 Supervisión de tareas y resiliencia de componentes

El daemon concentra en un único event loop: scheduler, ciclo del monitor, long-polling de Telegram, probe de red, heartbeat, descargas concurrentes y backfill. Sin disciplina explícita, cualquier excepción no capturada o tarea huérfana puede tumbar todo el proceso. Requisitos del MVP, no mejoras opcionales aplazables:

- **Toda tarea de fondo debe pasar por `create_supervised_task()`** — nunca llamar a `asyncio.create_task` directamente en ningún módulo del proyecto (la única excepción legítima es la definición del propio helper). Esta función envuelve la corrutina en try/except, loguea cualquier excepción con contexto estructurado, y registra un `add_done_callback` que audita el resultado (incluida cancelación).
- **Trampa T1 (crítica)**: el `add_done_callback` debe ser una función **síncrona** que lea `task.exception()` — un callback **async** pasado a `add_done_callback` crea la corrutina pero nunca la ejecuta (Python emite un warning de "coroutine never awaited" y la auditoría de la tarea nunca corre realmente). Este bug es fácil de introducir porque el resto del código del daemon es async-first. El logger de auditoría usa **stdlib logging** (interpolar el nombre en el mensaje — `logging` no acepta `name` como kwarg; lección L-B4, §21).
- **Trampa T30**: indexar las tareas supervisadas por `id(task)`, nunca por nombre — dos tareas con el mismo nombre lógico (p. ej. dos backfills concurrentes con etiquetas similares) se pisaban entre sí en un índice por nombre.
- Ninguna excepción de una tarea de fondo debe propagarse silenciosamente ni matar el event loop principal.
- Exponer un contador o listado de tareas supervisadas activas en `tikdown-rs daemon status` para diagnóstico (F-08).

### 5.8 Observabilidad de contención SQLite

- Un **listener `handle_error` a nivel del engine de SQLAlchemy** captura cualquier `sqlite3.OperationalError: database is locked` de operaciones productivas e incrementa un contador en memoria + loguea el evento estructurado `db.busy_timeout`. Es preferible a envolver cada operación individual con try/except disperso por el código.
- El heartbeat persiste el contador en `daemon_state.db_busy_count_5min` usando una **ventana rotativa real de 5 minutos** (F-08). Si supera `DB_BUSY_TIMEOUT_ALERT_THRESHOLD` (default 20) en esa ventana, emitir `daemon.db_contention` por Telegram (con dedupe por flanco para no repetir la alerta en cada heartbeat).
- `tikdown-rs daemon status` y `daemon healthcheck` **leen ese contador desde `daemon_state`**, nunca desde el propio proceso CLI (trampa T19: el contador de un proceso CLI de un solo disparo siempre sería 0, porque él mismo no acumula contención entre invocaciones).

---

## 6. Bot de Telegram (interfaz remota)

### 6.1 Integración en el loop del daemon

`getUpdates` con `timeout=25` (elección propia del proyecto — ni PTB ni la Bot API recomiendan un valor oficial; el default de PTB es 10), corriendo dentro del mismo event loop que el resto del daemon — no en un hilo separado ni con su propio loop. **Trampa T10**: no usar `run_polling()` (lanza `RuntimeError: event loop already running` dentro de un loop ya activo); usar en su lugar:

```python
await app.initialize()
await app.start()
await app.updater.start_polling(timeout=25)
# ... apagado:
await app.updater.stop()
await app.stop()
await app.shutdown()
```

(secuencia confirmada en la documentación oficial de PTB: "manual Application lifecycle".)

**Nota operativa (T71)**: la Bot API solo permite UNA sesión de `getUpdates` simultánea por bot — una llamada manual de diagnóstico (`getUpdates` vía curl/script) mata el polling en curso con `Conflict` (409, `telegram.error.Conflict`) y el bot queda muerto en silencio (el daemon no lo detecta; sigue healthy con el bot muerto — solo visible en logs). Verificar el bot SIEMPRE con `getMe`/`sendMessage`; la recuperación es reiniciar el contenedor. *Matiz verificado externamente (PTB issue #3430): en PTB ≥20 la captura del `Conflict` vía `add_error_handler` es poco fiable — la supervisión del polling del backlog §18 debe implementarse y **verificarse empíricamente** contra la versión pineada antes de darla por válida.*

La `Application` se construye con `Application.builder().token(<TELEGRAM_BOT_TOKEN>)` (§12; su ausencia con modo `commands`/`both` es error de configuración en `validate_for_daemon()`, T25) y el rate limiter de §6.3. **`AIORateLimiter` exige el extra `[rate-limiter]` de python-telegram-bot** (`aiolimiter`) — sin el extra, la construcción del bot revienta con `RuntimeError` (lección L-H2, §21; ya reflejado en el stack, §1).

**Requisito de diseño no negociable (trampa T26)**: el bot debe crear **un único engine async de SQLAlchemy** al arrancar y reutilizarlo (vía `async_sessionmaker`) durante todo su ciclo de vida, e igualmente debe recibir el `DownloadEngine`, el motor de descargas, el archive y la clave Fernet **inyectados por el daemon en el constructor**, nunca creados por comando recibido. Crear un engine nuevo por comando provoca fugas de conexiones y contención bajo uso normal — este es el motivo por el que la CLI y el daemon comparten la misma disciplina de sesiones desde `core/db.py`, y el bot debe seguir exactamente el mismo patrón. En uso standalone (sin inyección explícita), las dependencias se resuelven una sola vez en el constructor — nunca perezosamente por handler — y un flag `owns_engine` decide si el bot debe disponerlas al terminar.

**Entrega at-least-once de updates (trampa T48)**: PTB mantiene el offset de `getUpdates` en memoria y confirma los updates al completar sus handlers; tras un reinicio, Telegram puede re-entregar updates no confirmados. Si el proyecto quiere el offset en SQLite (§5.4), debe implementarlo explícitamente — PTB no lo hace por sí mismo. En cualquier caso, todos los handlers deben ser idempotentes ante una re-ejecución (el throttle de 30s de `accounts check` ya proporciona idempotencia de facto en ese caso).

### 6.2 Modo del bot

```env
TELEGRAM_BOT_MODE=both   # notifications | commands | both
```

### 6.3 Seguridad y throttle

- Solo `TELEGRAM_CHAT_ID` puede ejecutar comandos; cualquier otro chat → evento `bot.unauthorized_attempt` (warning, auditoría por log, registrando `chat_id` y `from_user.id`). Esta verificación se aplica en comandos, **callbacks de botones inline Y documentos subidos**, no solo en el dispatcher de comandos de texto. **Además del chat se verifica `from_user.id`** (configurable vía `TELEGRAM_USER_ID`, lista separada por comas; por defecto el propietario del `TELEGRAM_CHAT_ID` configurado): si el chat permitido fuera un grupo, cualquier miembro tendría control total del daemon — el filtro por chat solo es suficiente en chats privados. El guard debe tolerar updates sin `effective_chat` (None) sin reventar (F-18).
- Throttle: 1 comando cada 2s por `chat_id`, aplicado también en callbacks (F-18, con `query.answer()` para cerrar el spinner del botón).
- Botones inline con expiración **real**, no solo visual: timestamp embebido en `callback_data` y validado en el handler antes de ejecutar la acción (60s).
- **Límite de tamaño de upload de cookies**: 10 MB, verificado tanto por el metadato remoto (tamaño reportado por Telegram) **como** por el tamaño real post-descarga del archivo — el metadato puede llegar en 0, ausente o manipulado.
- Uploads a tempfile con `mkstemp` (permisos 0600) y borrado garantizado en `finally`. **Cerrar el fd con `os.close(fd)` inmediatamente tras `mkstemp`** (lección L-H5, §21): un fd abierto impide el `unlink` en Windows (`PermissionError: being used by another process`) y deja el tempfile huérfano.
- **Presupuesto de `callback_data` (trampa T38)**: Telegram limita `callback_data` a **64 bytes**; el diseño de botones con expiración real (timestamp + acción + argumentos) debe usar un encoding compacto presupuestado (acción corta, timestamp epoch, payload acotado) — un `callback_data` que excede el límite falla al **crear** el botón, no al pulsarlo.
- **Límite de 4096 caracteres por mensaje (trampa T39)**: `/list`, `/cookies`, `/stats` y notificaciones largas deben trocearse o truncarse con indicación; un mensaje que excede el límite provoca `Message_too_long` y, al ser el envío best-effort, se perdería en silencio. **Implementación obligatoria (F-07)**: helper `clip()` **compartido** entre el bot y el servicio de notificaciones, con el sufijo de truncado DENTRO del límite (`text[:limit-len(sufijo)] + sufijo`, 4096 exactos máximo) — un truncado que añade el sufijo FUERA del límite produce `Message_too_long` y, peor, una entrada "venenosa" en el spool que reintenta para siempre. Los errores definitivos de API (BadRequest) se descartan sin re-spolear.
- **Formato de mensajes: `parse_mode=HTML` + `html.escape()` sobre TODO contenido dinámico** (títulos, usernames, descripciones — trampa T40). MarkdownV2 queda prohibido: sus caracteres reservados (`_ . ( ) # !` ...) aparecen de forma rutinaria en contenido de TikTok, y un `can't parse entities` en un canal de envío best-effort es una notificación perdida en silencio. Alternativa válida: texto plano sin parse mode. **Implementación obligatoria (F-05)**: el escape se aplica en `event_message()` (servicio de notificaciones) Y en los handlers del bot (`_esc()` en `/list`, `/cookies`, `/stats`, `/check`, `/add`, `/pause`, `/resume`, `/notify`, `/backfill`); además, degradación a **texto plano** si Telegram responde `BadRequest: can't parse entities` (defensa en profundidad ante cualquier contenido que escape al escapado).
- **Rate limiting (trampa T41)**: el bot se construye con `Application.builder().rate_limiter(AIORateLimiter(max_retries=3))` y el servicio de notificaciones usa `ExtBot` (no `Bot` plano) con rate limiter — los defaults de `AIORateLimiter` ya son los límites oficiales de Telegram (30 msg/s global, 20 msg/min por grupo). Sin esto, una ráfaga de `download.completed` durante un backfill con `notify_on_download` produce 429 y pérdida de eventos.

### 6.4 Comandos (paridad funcional, no textual, con la CLI)

`/start /help /list /add /stats /disk /status /monitor /check /backfill /pause /resume /remove /cookies /notify /last`. El dispatcher **solo orquesta** funciones de `services/*`; `/check` y `/backfill` usan el motor inyectado con la clave real (no simulada — trampa T20). *Nota de alcance: la paginación real de `/list` (botones con offset para listas largas) queda en el backlog (§18); el MVP puede limitar a N resultados con un aviso.* Cualquier respuesta con interpolación de nombres de usuario normaliza `lstrip('@')` (el doble `@` en `/check` fue una lección real, L-H6 en §21).

---

## 7. Gestión de cookies

- Import por CLI (**vía preferente**) o por archivo subido al bot; detección de formato por contenido (Netscape 7 campos separados por TAB / JSON como lista / cookie-string), con conversión siempre a Netscape canónico internamente; validación de presencia de `sessionid` (CSRF preferible si está disponible). **Privacidad del import por Telegram**: una cookie de sesión equivale a acceso a la cuenta, y subirla como documento la hace transitar por los servidores de Telegram y queda en el historial del chat — documentarlo en el README, recomendar el import por CLI para cookies sensibles y, si se usa el bot, borrar el mensaje del documento tras importarlo (`deleteMessage`, best-effort) además del tempfile. Además, **advertir (no rechazar) si falta `sid_tt`**: el extractor propaga `sid_tt` de las cookies web al host de la API y a los hosts de los formatos; su ausencia no invalida la importación pero degrada rutas de extracción — queda registrado como diagnóstico en la cookie importada.
- Cifrado inmediato con Fernet tras la importación; borrado del archivo fuente **best-effort** (si falla, se advierte pero la importación se sigue reportando como éxito); flag `--keep-source` para conservarlo (F-15).
- **Tempfiles (trampa T31)**: `mkstemp` con la ruta asignada **justo tras la creación** del archivo temporal, con limpieza en `finally` que cubre también el caso de que la escritura falle a medias — si el `write()` no termina, la ruta debe eliminarse igualmente desde el propio worker que la creó, no depender de limpieza externa. Y `os.close(fd)` inmediato tras `mkstemp` (lección L-H5, §21 — en Windows un fd abierto impide el borrado).
- **Sesiones cortas durante validación (trampa T32)**: `test_cookie()` / `get_working_cookie()` deben leer el blob cifrado, **cerrar la sesión de base de datos**, validar con yt-dlp completamente fuera de esa sesión (llamada de red potencialmente lenta), y solo reabrir sesión para persistir el resultado. Mantener una sesión SQLite abierta mientras se espera una llamada de red externa es una violación del principio de sesiones cortas (§16) con consecuencias reales de contención.
- **Archivo Netscape reconstruido (trampa T73 — lección crítica L-E1, §21)**: `MozillaCookieJar._really_load` de CPython (el parser que usa `YoutubeDLCookieJar` de yt-dlp) exige que la PRIMERA línea case con `# Netscape HTTP Cookie File`. El blob cifrado guarda solo las líneas de cookies; todo archivo temporal reconstruido debe escribirse con el magic header (constante `NETSCAPE_HEADER` en `core/cookie_parser.py`, compartida con `write_netscape_file`, sin duplicar si el texto ya lo trae — cubre blobs viejos) y con `newline="\n"` explícito (en Windows el modo texto escribía CRLF y ensuciaba valores con `\r`). Un parser propio tolerante **enmascara** el rechazo del parser real de la librería: los tests de cookies deben cargar el tempfile regenerado con el `YoutubeDLCookieJar` REAL (carga local, sin red) — nunca solo con el parser propio.
- **Validación en tres estados, no dos**: solo un fallo de **autenticación confirmado** produce `validation_state='invalid'`; cualquier otro resultado (error de extractor, error de red, timeout) produce `'inconclusive'` y **no toca** `validation_state` ni `last_validated_at` (F-16: un `inconclusive` no debe desarmar la revalidación activa) — permite autocuración en el siguiente ciclo sin invalidar una cookie que probablemente sigue siendo válida. Un perfil de validación que devuelve una respuesta sin entradas (`no entries`) es `inconclusive`, no `valid`: una respuesta degradada no debe mantenerse silenciosamente como confirmación de validez.
- `get_working_cookie()`: cookies `valid` ordenadas por `last_validated_at` descendente; revalida activamente si el último chequeo es antiguo; con fallback a cookies anteriores si la primera falla; guarda contra el caso de que la cookie elegida se elimine concurrentemente durante el proceso. **`get_working_cookie` solo rechaza una cookie ante un resultado `invalid`** (lección L-E3, §21): ante un `inconclusive` la conserva y la usa (con log informativo) — tratar `inconclusive` como fallo hacía que una cookie perfecta fuera rechazada y que backfill/monitor abortaran con `no_cookies` teniendo una cookie válida.
- **Countdown dedicado**: implementar un helper específico de "segundos restantes hasta" (positivo si es futuro, 0 si ya pasó) para mostrar en `cookies list` — no reutilizar el helper inverso de "tiempo transcurrido desde", que da resultados con signo invertido para fechas futuras.
- **Espaciado del ciclo de validación**: el job de 6h valida las cookies **secuencialmente con 30-60s entre sondas** (o respetando el cooldown global) — cada validación es una extracción de perfil real (~2 requests), y un inventario grande validado en ráfaga es exactamente el patrón de peticiones que el cooldown global existe para evitar.
- **Clamp de fechas de expiración absurdas (trampa T33)**: `min(timestamp, fecha_maxima_razonable)` (p. ej. año 2100) antes de convertir a `datetime` — algunas cookies exportadas traen expiraciones con timestamps corruptos o desproporcionados que revientan `datetime.fromtimestamp` con `OverflowError` si no se acotan antes.
- Verificar/corregir permisos `0600` de `fernet.key` también sobre una clave ya existente al cargarla, no solo al generarla (trampa T7, ver §1 y §0.1).

**Sonda de validación (`COOKIE_VALIDATION_URL`)**: validar una cookie consiste en extraer **un perfil real y público** de TikTok usándola y observar los marcadores de §4.3 (redirect a `/login`, `requiring login`, `log into an account`, códigos 10216/10222). La sonda **debe** ser un perfil real: un perfil inexistente, eliminado o renombrado produce respuestas que no permiten distinguir "cookie inválida" de "sonda rota". Por eso el perfil-sonda es **configurable y fácilmente reemplazable** vía la variable de entorno `COOKIE_VALIDATION_URL` (§12), con un valor por defecto **verificado en producción** (`COOKIE_VALIDATION_URL_DEFAULT` = **`@rosary657`**, decisión consolidada en §20; el cambio toma efecto en el siguiente ciclo de validación tras reiniciar el daemon. **La sonda itera las primeras `PROBE_MAX_ENTRIES=5` entradas del feed** buscando una con formatos de vídeo; solo si NINGUNA de las 5 los tiene devuelve `inconclusive` (lección L-E4, §21: si la primera entrada es un slideshow solo-audio — frecuente incluso en perfiles buenos —, una sonda que solo inspecciona la primera entrada produce `inconclusive` permanente con cookies válidas). Criterios de elección del perfil-sonda:
  - Público y activo, con historial largo y actividad reciente (baja probabilidad de borrado, suspensión o renombrado).
  - Sin restricción regional ni clasificación de audiencia (un contenido `isContentClassified` exigiría login incluso con cookie sana → falso `invalid`).
  - Contenido neutro (aparecerá en logs y notificaciones).
  - **Embedding habilitado y formatos extraíbles verificados (trampa T74, hallazgo de producción)**: una cuenta oficial grande (p. ej. `@tiktok`) puede tener el embedding deshabilitado — el extractor avisa *"This user's account is either private or has embedding disabled"* y el primer vídeo del feed devuelve `No video formats found` → la sonda produce falsos `inconclusive` con cookies VÁLIDAS. El sistema se comporta bien (no invalida), pero la sonda queda inútil. VERIFICAR la sonda elegida con una extracción real antes de desplegarla: `yt-dlp -s <perfil>` debe devolver formatos para su primer vídeo. Nota: "No video formats found" es un fallo conocido del extractor (issues upstream #12441/#11701/#12610; en feeds con posts de solo foto, #12610, es el comportamiento esperado del caso slideshow T55), así que la verificación debe repetirse al cambiar de nightly.

- **Rotación de sondas y punto único de fallo externo (R12, §25.5)**: `COOKIE_VALIDATION_URL` acepta una **lista separada por comas** de perfiles-sonda candidatos. El ciclo de validación los itera en orden: solo si la sonda activa falla con marcadores de sonda rota (contenido inexistente, §4.3 punto 4; o `inconclusive` estructural tipo T74 — primera entrada slideshow en las 5 inspeccionadas, embedding deshabilitado) pasa a la siguiente candidata, y solo declara `cookie.validation_probe_failed` cuando **todas** las candidatas fallan así. Motivo doble: (a) el default es una cuenta pública de un tercero (`@rosary657`) hardcodeada en un repositorio público — un punto único de fallo externo: si muere o se renombra, todos los despliegues que no la sobrescriban degradan su validación a la vez; (b) consideración operativa y ética: esa cuenta recibe el tráfico de sondas de todos los despliegues que usen el default. El README debe recomendar que cada despliegue configure 2-3 sondas propias (perfiles públicos verificados con `yt-dlp -s`, criterios de arriba), repartiendo ese tráfico y eliminando la dependencia del default.

**La sonda rota nunca invalida cookies (trampa T57)**: si la extracción de la sonda falla con marcadores de contenido inexistente (`404`, `video unavailable`, `status code` desconocido), o el ciclo devuelve `inconclusive` para **todas** las cookies a la vez, la causa probable es la sonda o la red, no las cookies → el ciclo entero se registra como `inconclusive` global, no se toca ningún `validation_state`, y se emite `cookie.validation_probe_failed` por Telegram sugiriendo actualizar `COOKIE_VALIDATION_URL`. Un patrón "todas las cookies inválidas simultáneamente" debe tratarse siempre con sospecha de sonda o de red, nunca como invalidez masiva real.

---

## 8. Notificaciones y eventos

**Catálogo con productores reales**: cada evento del catálogo debe tener un punto concreto del código que lo emite, no solo una plantilla de texto — la ausencia de un productor real para un evento "documentado" es un evento que en la práctica nunca se envía. El catálogo vigente (el evento `backfill.paused` está **retirado**: no existe mecanismo de pausa de backfill; si se implementa la recogida de pausados del backlog §18, se reintroduce):

| Ámbito | Eventos |
|---|---|
| Monitor | `monitor.new_videos_found`, `monitor.account_paused` (emisor: circuit breaker, F-08), `monitor.account_unreachable`, `monitor.stopped_no_cookies`, `monitor.disk_warning` (productor: job de chequeo de disco de §5.1 — trampa T65), `monitor.yt_dlp_update_available` (una sola vez por versión nueva — dedupe vía `daemon_state.last_notified_ytdlp_version`; el nightly publica a diario y sin dedupe la alerta sería diaria), `monitor.account_health_check_failed` (emisor: job de refresco de perfil, F-08) |
| Descarga | `download.completed` (opt-in por cuenta vía `notify_on_download`, emitido por el monitor **y por backfill** — incluido `retry-failed`, propagando `notify_on_download` desde la cuenta, L-G3), `download.failed` (fallo terminal: definitivo, o integridad tras agotar el reintento de formato mejorado — siempre accionable), `download.retry_exhausted` (transitorio persistente que agotó `MAX_VIDEO_RETRY_COUNT` — accionable, sugiere `retry-failed`), `download.format_retry` (F-08) |
| Backfill | `backfill.completed`, `backfill.cancelled`, `backfill.queued` (emitido por el job de recogida del daemon al reclamar la cola, F-08), `backfill.no_cookies` (F-01), `backfill.monitor_activated` (§10: transición automática history→monitor) |
| Red / daemon | `network.offline` (una sola vez al confirmar la caída), `network.online` (solo tras recuperación **desde** offline, con la duración de la caída), `daemon.started`, `daemon.stopped`, `daemon.db_contention` (ventana rotativa de 5 min, §5.8), `daemon.selfcheck_broken_after_update` (§4.1) |
| Cookies / cuentas | `cookie.validation_invalid` (solo ante un `invalid` real, nunca ante `inconclusive`), `cookie.validation_probe_failed` (la sonda de §7 falló — accionable: revisar `COOKIE_VALIDATION_URL`), `account.needs_review` |
| Bot | `bot.unauthorized_attempt` (auditoría por log, no necesariamente notificación push; canal de eventos del daemon inyectado al bot, F-08), `bot.unauthorized_attempts_burst` (§22.4 — emisor: contador en memoria del proceso del bot, ≥5 intentos no autorizados del mismo `from_user.id` en 5 min; SIEMPRE notificación push, nunca solo log, por ser indicio de tanteo activo) *[Añadido en el análisis posterior]* |

- El servicio de envío usa `ExtBot(token, rate_limiter=...)` directo (no requiere una `Application` completa — ver §6.3); los errores de envío **nunca** bloquean el flujo principal que los originó (captura amplia `except Exception`, no solo `TelegramError` — los fallos de red pueden surfacear como cualquier excepción; lección L-I1, §21). Noop por defecto (`ENABLE_EXTERNAL_NOTIFICATIONS=false`). Con el servicio Noop no se crean tareas de envío (solo log) — crearlas inflaba el contador de tareas activas en el apagado (lección L-B5, §21).
- **Spool de notificaciones (trampa T42)**: ante un fallo de envío por falta de red, el evento se persiste en la tabla `pending_notifications` (§2) en vez de descartarse — **solo cuando `ENABLE_EXTERNAL_NOTIFICATIONS=true`** (con el servicio Noop no se espolea nada: no habría drenaje posible y la tabla crecería en silencio), y se drena en la transición a `online` (§9) y en el arranque del daemon. Motivo: la notificación más importante de todas — `network.offline` — no puede entregarse precisamente cuando ocurre, porque el propio envío necesita la red caída. El mensaje de `network.online` ya lleva la duración de la caída (T35), así que drenar es entregar los eventos pendientes con su timestamp original, marcados como entrega diferida. **El spool guarda el evento ORIGINAL (event, payload), nunca el texto renderizado (F-06)**: el drenaje re-renderiza con `event_message()` (ya escapado); spolear el JSON crudo del texto producía mensajes corruptos y consolidaba el bug en el test. El spool está acotado (FIFO, p. ej. 100 eventos; se descartan primero los no críticos).
- **Coalescing en ráfagas (requisito MVP)**: cuando las completadas de una misma cuenta superan un umbral por ventana (p. ej. >5 en 10 min durante un backfill), el servicio de notificaciones agrupa en un único mensaje resumen ("N descargas completadas en @cuenta en los últimos M min") en vez de un mensaje por vídeo — complementa al rate limiter de §6.3, que protege la API pero no la legibilidad del chat. **El resumen se genera con condición `>=` umbral y bandera consumible** (una sola emisión por ventana; la condición `==` exacta perdía la ráfaga si el consumidor consultaba después — lección L-I3, §21).
- **Regla de notificaciones de descarga**: `download.failed` y `download.retry_exhausted` son siempre accionables (notifican cuando las notificaciones están habilitadas — principio 9), nunca opt-in; `download.completed` es el único opt-in por volumen. **Toda notificación de descarga lleva contexto accionable completo (trampa T61)**: las de fallo incluyen cuenta, **URL directa del vídeo en TikTok**, categoría (`definitive`/`transient`/`integrity`), motivo legible (en fallos de integridad, el detalle de ffprobe: sin pista de vídeo / duración 0 / codec indetectable), `retry_count` y el siguiente paso sugerido (p. ej. `backfill retry-failed @user`); las de éxito incluyen cuenta, título truncado, **URL directa del vídeo en TikTok** (para comparar visualmente el reel con lo archivado), ruta local, tamaño, y duración/resolución/codecs verificados por ffprobe en §4.6 — la duración mostrada permite cotejar de un vistazo que el reel y el archivo son el mismo contenido, y la verificación previa garantiza que el archivo local es reproducible antes de notificar.
- **Las plantillas ya incluyen el literal `@` delante de `{username}`**: el render NO debe añadir otro `@` al escapar el username (lección L-H7, §21 — todas las plantillas producían `@@usuario`).
- **La paridad plantilla ↔ productor se verifica con tests explícitos**: un evento que se emite pero no tiene plantilla en `event_message()` (o viceversa) queda silenciosamente sin notificar aunque el código de negocio "cumplió" su parte — esta clase de bug es fácil de introducir al añadir un evento nuevo sin actualizar ambos lados a la vez. El test de paridad **excluye `events.py` del scan de literales** (F-08): el catálogo vive en ese módulo, y sin la exclusión la segunda aserción no podía fallar (test vacuo).
- **Propagación del canal de eventos (trampa T75)**: todo job o llamada que lanza una corrutina con canal de eventos debe propagarlo explícitamente (`on_event=on_event`). Un backfill lanzado por el job de recogida con el canal en `None` pierde silenciosamente TODOS sus eventos (completado, monitor_activated, no_cookies, download.*) — lección L-I5, §21.

---

## 9. Red: probe, pausa y reanudación automáticas

- Máquina de estados simple: `online` / `offline` (con un estado transitorio implícito mientras se acumulan fallos antes de confirmar `offline`).
- Probe HEAD (`httpx`, timeout `NETWORK_PROBE_TIMEOUT_SECONDS`, default 5s — **cableado desde Settings, nunca hardcodeado**; F-13) a endpoints neutrales configurables (`NETWORK_PROBE_URL`, lista separada por comas; **nunca** un dominio de TikTok). Usar defaults razonables solo si la lista viene vacía.
- Tras `NETWORK_OFFLINE_THRESHOLD_CONSECUTIVE_FAILURES` fallos consecutivos (default 2 — un único HEAD fallido no debe considerarse una caída real) → transición a `offline`, `network_available.clear()`, notificación única.
- Mientras está `offline`, el intervalo entre probes crece con backoff (30s → techo 120s, con jitter) para no insistir agresivamente sin necesidad — **el job del probe se re-programa tras cada ciclo** con `wait_between_probes` (offline) o el intervalo configurado (online) (F-13).
- Al primer probe exitoso → vuelve a `online` inmediatamente, `network_available.set()`, notificación con la duración de la caída.
- **Transición de estados con notificación correcta (trampa T35)**: capturar la duración de la caída **antes** de limpiar el timestamp `offline_since` en la transición a `online` — si se limpia primero, la duración se pierde. Además, notificar `network.online` **solo** si el estado inmediatamente anterior era `offline` confirmado: una transición de "probando caída" de vuelta a `online` sin haber llegado a confirmarse offline (un simple "blip" de red) no es una recuperación real y no debe generar una notificación de "de vuelta online" engañosa.
- **Drenaje del spool**: en la transición a `online` confirmado (y solo entonces, por la misma regla de T35), se drena `pending_notifications` en orden de `created_at` antes de reanudar el resto de trabajos — ver §8.
- El evento `network_available` (`asyncio.Event`) se **inyecta** en el motor de descargas, el monitor y los jobs relevantes — nunca como singleton global implícito. El motor de descargas espera `network_available.wait()` antes de **cada** intento, incluidos los reintentos. **El evento por defecto del motor se crea YA SETEADO** (lección L-D2, §21): un `asyncio.Event()` por defecto nace en clear (=offline) y, sin `NetworkMonitor` inyectado, nadie lo activaría nunca — `engine.download()` colgaría indefinidamente; sin monitor, la red se asume disponible. El monitor salta iteraciones completas sin marcar fallos de cuenta durante ese tiempo. Los jobs de validación de cookies y refresco de perfil se saltan mientras dure la caída. **Un fallo de red nunca debe producir `validation_state='invalid'`** en una cookie — sería invalidar un recurso sano por un problema ajeno a él. Por la misma regla, un fallo de red tampoco incrementa `retry_count` ni consume el presupuesto de reintentos/tiempo de un vídeo (§4.4, trampa T64).
- `tikdown-rs daemon status` expone el estado de red del daemon en la medida en que la coordinación por base de datos lo permite (limitación documentada explícitamente: el estado en memoria del daemon no es visible desde otro proceso sin que el propio daemon lo persista activamente en su heartbeat).

---

## 10. Backfill y regla de concurrencia

- **Cookies obligatorias (F-01)**: el backfill y `retry-failed` descargan CON cookies — adquieren `get_working_cookie()` al inicio (o usan el `cookiefile` inyectado con prioridad) y abortan con el evento `backfill.no_cookies` si no hay ninguna (espejo del §5.3). El listado de feeds (`list_videos`/`extract_profile`) acepta `cookiefile` y monitor/backfill/CLI foreground lo usan. (El parámetro muerto que no llegaba al embudo fue una lección real, L-F9 en §21.)
- Slot único de backfill activo por proceso (su adquisición no bloqueante usa el patrón de la trampa T11: `if lock.locked(): return False; await lock.acquire()` — nunca `wait_for(lock.acquire(), timeout=0)`). **El daemon recoge backfills `queued` automáticamente** (job del scheduler de §5.1): si no hay backfill activo (`backfill_slot_busy()` falso), reclama el siguiente `queued` por orden de llegada y lo ejecuta con el slot del daemon — así `backfill run @user` desde CLI puede lanzar en foreground (proceso CLI) o encolar para el daemon (`--queue`), y ningún backfill encolado se queda sin ejecutor. Limitación documentada residual: el slot no es una barrera cross-proceso (un backfill foreground de CLI y uno del daemon podrían solaparse), pero el pacing **sí** es cross-proceso (§4.5), así que la tasa agregada de peticiones sigue acotada aun en ese caso. **Throughput esperado (documentar al usuario al lanzar)**: ~40-150s por vídeo con los defaults (cooldown 30-120s + descarga) — un backfill de 1.000 vídeos tarda del orden de 11-42h; es reanudable por diseño (cursor + archive) y el comando muestra esa estimación antes de empezar.
- Cursor estricto por `upload_date`, con comparación estrictamente `<` (nunca `==`, para evitar perder o repetir el vídeo exactamente en el borde del cursor), complementado con `--download-archive` como deduplicación real. El cursor **solo avanza cuando el vídeo alcanza un estado terminal** (`downloaded`, `failed` con categoría, o `skipped`): si avanzara tras "procesado" a secas, un vídeo interrumpido a mitad quedaría fuera del alcance de la reanudación y solo lo rescataría `retry-failed` si quedó registrado. El `break` del feed usa un `scope_cursor` (snapshot del progreso previo) mientras el cursor móvil avanza por vídeo procesado — confundirlos detiene el backfill tras el primer vídeo (lección L-F1, §21).
- **Vídeos `status='cancelled'`: semántica y productor de reintento (ambigüedad resuelta — R9, §25.5)**: un vídeo `cancelled` (interrumpido por apagado, §5.2 paso 6) **no es terminal para el cursor** — la lista de estados terminales es `downloaded`/`failed`/`skipped`, y `cancelled` no está en ella: el cursor no avanza sobre él —, **no se escribe** en el archive de deduplicación y **no lo recoge `retry-failed`** (cuyo dominio es `failed`). Su reintento tiene productor explícito: la re-ejecución del backfill de su cuenta. Al interrumpirse por apagado, el backfill vuelve a `queued` (F-10), la reconciliación de arranque (§5.1) y el job de recogida lo relanzan, y la pasada reanudada redescubre el vídeo por el feed (cursor no superado, sin entrada en archive) y lo reintenta sin acción manual. En modo `monitor`, la diferencia de conjuntos contra `download_archive` hace lo mismo. Un `backfill cancel` manual deja el backfill en `cancelled` (no se re-encola): esos vídeos se reintentan en la siguiente ejecución de `backfill run` de esa cuenta.
- **Contabilidad del progreso**: `backfill_total` se persiste **al iniciar cada pasada** (F-09: el `done` es acumulativo — los ya archivados se saltan sin contar, así `done/total` converge); `backfill_done` cuenta todo vídeo que alcanza **estado terminal** — incluido `skipped` (un slideshow contabiliza como procesado; sin esto, el progreso nunca alcanzaría `backfill_total` en cuentas con slideshows).
- **Robustez ante interrupciones (F-10)**: el listado del feed va DENTRO del try catástrofe (un fallo del feed no debe dejar el backfill wedged en `backfilling`); `except asyncio.CancelledError` (BaseException — no lo cubre `except Exception`) devuelve el backfill a `queued` (auto-reanudable); `reconcile_stale_backfills()` en el arranque del daemon devuelve a `queued` todo backfill huérfano en `backfilling` (crash o shutdown); el job de recogida comprueba `backfill_slot_busy()` antes de crear la tarea (ya no apila tareas que morirían con `BackfillBusy`) y **propaga el canal de eventos** (T75).
- **Cancelación real (trampa T21)**: el comando `backfill cancel` cambia `backfill_status` a `'cancelled'` en la base de datos; el worker de backfill **relee ese estado periódicamente durante la ejecución** y, si detecta que ya no es `'backfilling'`, detiene el bucle de forma cooperativa. Sin esta relectura activa, el comando `cancel` solo escribiría en la base de datos mientras el proceso de backfill seguiría descargando indefinidamente en background, ignorando por completo la señal. **Tres detalles verificados (lecciones L-F5/L-F6/L-F7, §21)**: (a) la persistencia del cursor tras cada vídeo usa un **UPDATE condicional** con `WHERE backfill_status='backfilling'` — `rowcount 0` significa cancelación detectada, sin sobrescribir nada (un read-then-write en dos transacciones deja una ventana donde la reescritura de `'backfilling'` pisa el `'cancelled'` concurrente); (b) tras una cancelación cooperativa, el flujo retorna el outcome `cancelled` inmediatamente — sin ejecutar la transición `--then-monitor` ni emitir `backfill.completed`; (c) el CHECK constraint del esquema incluye `'cancelled'` desde la migración inicial (§2).

**Transición automática history → monitor (`--then-monitor`)**: cuando un backfill termina con `backfill_status='completed'` en una cuenta con `monitor_after_backfill=1`, la transición se ejecuta en la **misma transacción** que marca el backfill como completado, con condición idempotente (`UPDATE ... SET mode='monitor', monitor_after_backfill=0 WHERE id=? AND mode='history' AND monitor_after_backfill=1 AND backfill_status='completed'`). La bandera es **consumible**: un backfill posterior no re-transiciona salvo nueva activación explícita. Se emite `backfill.monitor_activated`. La transición **solo** ocurre desde `'completed'` — nunca desde `'failed'` ni `'cancelled'` (un backfill fallido queda en su dominio: `retry-failed`). Un backfill completado *con* algunos vídeos fallidos transiciona igualmente: esos vídeos ya tienen su vía de reintento.
- **La transición no arranca el monitor global (trampa T60)**: solo cambia el `mode` de la cuenta; el interruptor de §5.1 (el monitor siempre arranca detenido, `MONITOR_AUTOSTART`) sigue siendo la puerta de seguridad — la cuenta se incorpora al ciclo cuando el monitor esté corriendo. Activar el monitor global como efecto lateral de un backfill violaría la decisión de seguridad de §5.1. El ciclo del monitor no necesita ningún cambio: re-consulta las cuentas en cada ejecución y recoge la nueva automáticamente.
- **Reconciliación en arranque (trampa T59)**: si la transición y el completado se escribieran por separado, un crash entre ambas perdería la transición — por eso van en la misma transacción, y además el arranque del daemon aplica defensivamente la misma `UPDATE` idempotente a toda cuenta con `monitor_after_backfill=1 AND backfill_status='completed' AND mode='history'`.
- **El modo `history` standalone se conserva deliberadamente** (no se funde en `backfill+monitor`): el archivado puntual sin seguimiento es un caso de uso legítimo, la maquinaria del auto-pase se apoya en la del backfill normal (retirarlo no simplificaría código, solo quitaría una opción), y mantiene el default conservador de no iniciar actividad continua sin consentimiento. Patrón validado contra el comportamiento por defecto de herramientas equivalentes (Pinchflat, TubeArchivist).
- **El pacing por vídeo del backfill es el cooldown global de §4.5** — no hay pausa corta adicional entre vídeos: quedaría subsumida por los 30-120s del cooldown y sería ruido redundante. Pausa larga (60-120s) cada ~50 vídeos procesados, para reducir la probabilidad de disparar el limitador anti-bot en tandas largas.
- `retry-failed` usa el **mismo** helper de integridad (`handle_download_result`, §4.6) que monitor y backfill — con descarte de archive, retry con formato mejorado y `base_retry_count` acumulado. Un archivo inexistente o corrupto **nunca** se marca `downloaded` solo porque `retry-failed` lo intentó.
- Descarte de la entrada del archive de deduplicación antes de cada reintento (§4.6) — defensivo y obligatorio en todas las rutas de reintento, no solo en la primera.

---

## 11. Despliegue (Docker únicamente)

- **Rutas**: `DATA_DIR=/app/data` dentro del contenedor. Un único volumen para base de datos, clave Fernet, archive de deduplicación **y vídeos** (`<DATA_DIR>/videos`) — todos comparten el mismo `VOLUME` persistente (trampa T8).
- **Dockerfile multi-stage en la raíz del proyecto** (`./Dockerfile`, patrón oficial de uv — lección L-K1 en §21; el patrón con builder distroless `ghcr.io/astral-sh/uv:latest` dejaba un venv con intérprete colgante y la imagen no arrancaba):
  - Stage `builder` sobre **`python:3.13-slim`** con los binarios de uv copiados (`COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/`), `UV_COMPILE_BYTECODE=1`, `UV_LINK_MODE=copy`, **`UV_PYTHON_DOWNLOADS=0`** (usa el CPython de la imagen base; sin esto uv descarga un intérprete gestionado que no existe en el runtime), instalando dependencias con `uv sync --frozen --no-editable --no-install-project` **antes** de copiar el código (para aprovechar caché de capas por lockfile) y el proyecto después. Variante igualmente válida según la documentación oficial: usar como builder la imagen `ghcr.io/astral-sh/uv:python3.13-...` (variante que ya incluye Python). Lo que queda **prohibido**: builder distroless `uv:latest` como base del build.
  - Stage `runtime` sobre `python:3.13-slim` con **`ffmpeg` instalado explícitamente** (`apt-get install -y --no-install-recommends ffmpeg` — dependencia dura, trampa T46) + solo el `.venv` ya resuelto (sin `uv` ni caché de build en la imagen final).
  - **Sintaxis ENV sin comentarios inline** (lección L-K3, §21): el parser de Docker no admite comentarios `#` dentro de una instrucción `ENV` continuada con `\` — los comentarios van en líneas propias antes de la instrucción.
  - `CMD ["tikdown-rs", "daemon", "run"]`. `HEALTHCHECK` apuntando a `tikdown-rs daemon healthcheck` (exit 0 solo con heartbeat fresco; `--start-period` ≥ duración del selfcheck de arranque — T50).
- **`docker-compose.yml` en la raíz** con contexto de build `.` y `env_file: .env`; un solo servicio `tikdown-rs` con un único volumen para `DATA_DIR`.
- **`.dockerignore` obligatorio y completo** (trampa T15, ver §0.1) — sin él, `COPY . .` embebe secretos reales en capas de la imagen recuperables. Debe **re-incluir `README.md`** (F-04: hatchling lo exige para construir el wheel).
- **Build multi-arquitectura**: `docker buildx build --platform linux/amd64,linux/arm64`. **El selfcheck de impersonación es obligatorio en el hardware de destino real antes de comprometerse a operar en modo `monitor` de forma intensiva**, especialmente en ARM64 — no asumir que funcionará por haber funcionado en `amd64` (§4.1).
- WebDAV opcional como sidecar — ver §17 y §23.4.
- **Rotación de logs del contenedor, obligatoria *[Añadido en el análisis posterior]***: el logging JSON a stdout (§1, principio 8) sin límites de rotación crece sin techo bajo el driver `json-file` por defecto de Docker — en un daemon de larga duración (por diseño, §0) esto es exactamente el mismo patrón de fallo silencioso por disco lleno que el proyecto ya trata como caso de primera clase para vídeos y base de datos (T45, `system disk`, §4.4 punto 6, §5.1), solo que aquí no lo cubre ningún job existente porque los logs quedan fuera de `DATA_DIR` y del monitoreo de espacio libre de §5.1/§9. Fijar en `docker-compose.yml`:
  ```yaml
  services:
    tikdown-rs:
      logging:
        driver: "json-file"
        options:
          max-size: "10m"
          max-file: "5"
  ```
  Esto acota el log a un máximo de ~50 MB totales por contenedor, independiente del `DISK_WARNING_FREE_PERCENT` de §12 (que vigila `DATA_DIR`, no los logs de Docker). No requiere cambios en la aplicación: es configuración pura de despliegue.

> **Nota**: el despliegue del proyecto se basa **únicamente en Docker**. El hardening de contenedor se asume a través de las opciones de Docker (`--read-only`, `read_only: true`, usuarios no root, `seccomp`, etc.) y de la guía de despliegue seguro de §22.5.



---

## 12. Configuración (pydantic-settings)

```env
DATA_DIR=/app/data
LOG_LEVEL=INFO
FERNET_KEY=                       # o se genera en DATA_DIR/fernet.key — NUNCA commitear un valor real

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_USER_ID=                 # opcional; lista separada por comas de from_user.id autorizados
                                  # (vacío = propietario del TELEGRAM_CHAT_ID, §6.3)
TELEGRAM_BOT_MODE=both            # notifications | commands | both
ENABLE_EXTERNAL_NOTIFICATIONS=false

MONITOR_INTERVAL_MINUTES=5        # intervalo del ciclo del monitor, en minutos
MONITOR_AUTOSTART=false           # el monitor siempre arranca detenido por defecto, ver §5.1

MAX_CONCURRENT_DOWNLOADS=1
GLOBAL_DOWNLOAD_COOLDOWN_MIN_SECONDS=30   # cooldown aleatorio uniforme [MIN, MAX] por descarga (§4.5, T62)
GLOBAL_DOWNLOAD_COOLDOWN_MAX_SECONDS=120  # MIN=MAX → fijo; ambos 0 → desactivado; MAX<MIN → error de config (T25)

YTDLP_ANTIBOT_BACKOFF_BASE_SECONDS=10
YTDLP_ANTIBOT_BACKOFF_CEILING_SECONDS=120
DOWNLOAD_FORMAT=                       # override opcional del formato por defecto de §4.2

DB_BUSY_TIMEOUT_ALERT_THRESHOLD=20

HEARTBEAT_INTERVAL_SECONDS=10     # intervalo del heartbeat; su frescura = 3× este valor (T50)
DISK_WARNING_FREE_PERCENT=10      # umbral de espacio libre del job de disco (monitor.disk_warning, §5.1)
SYSTEM_BACKUP_RETAIN_COUNT=7      # snapshots de `system backup` a conservar antes de purgar los más
                                  # antiguos (§23.3.6) [Añadido en el análisis posterior]

MAX_VIDEO_RETRY_COUNT=5           # techo de reintentos transitorios por vídeo (§4.4);
                                  # al alcanzarlo → status='failed' + download.retry_exhausted
MAX_VIDEO_TOTAL_TIME_SECONDS=900  # presupuesto de tiempo total por vídeo, incluidos todos sus
                                  # intentos inline (§4.4, T63); se agota lo que ocurra antes

COOKIE_VALIDATION_URL=             # sonda(s) de validación de cookies (§7): perfil REAL, público y
                                   # estable; acepta LISTA separada por comas con fallback en orden
                                   # (R12, §7). Default en código: @rosary657 (verificado en
                                   # producción, T74) — cuenta de terceros en un repo público:
                                   # configurar sondas propias es lo recomendado. Si una sonda muere
                                   # o se renombra, cambiar aquí y reiniciar el daemon
YTDLP_PROXY_URL=                   # opcional; lista separada por comas (rotación round-robin MVP, §4.7)
YTDLP_EXTRACTOR_ARGS=              # opcional; passthrough a extractor-args de yt-dlp
                                   # (p. ej. "tiktok:device_id=..."), válvula de escape ante
                                   # degradaciones futuras del feed sin tocar código

NETWORK_PROBE_URL=                 # endpoints neutrales, separados por coma; nunca TikTok
NETWORK_PROBE_INTERVAL_SECONDS=30
NETWORK_PROBE_TIMEOUT_SECONDS=5    # cableado al probe desde Settings (F-13), nunca hardcodeado
NETWORK_OFFLINE_THRESHOLD_CONSECUTIVE_FAILURES=2

# Sidecar WebDAV (rclone) — NO las consume la aplicación (F-17, §17); las lee el sidecar:
# WEBDAV_USER=...
# WEBDAV_PASSWORD=...
```

- **Fail-fast (trampa T25)**: `validate_for_daemon()` se ejecuta al arrancar el daemon y falla explícitamente antes de crear ningún recurso si, por ejemplo, hay notificaciones habilitadas sin token, modo `commands`/`both` sin `TELEGRAM_CHAT_ID`, o intervalos con valores inválidos (negativos, cero donde no corresponde; `MAX < MIN` en el cooldown).
- **Cada variable declarada debe tener efecto real (trampa T36)**: si una variable del `.env.example` no está realmente conectada a la lógica que promete controlar, o bien se conecta de verdad, o bien se retira del `.env.example` — una variable declarada mas no aplicada es documentación engañosa. **Ejemplo resuelto (F-17)**: el bloque `WEBDAV_*` se retiró de `Settings` — la aplicación no consume esas variables (las lee el sidecar `rclone`, que tiene su propio entorno); declararlas y validarlas en la app podía **bloquear el arranque del daemon** por una feature que la app no implementa. En `.env.example` quedan documentadas como variables del sidecar (ver §17), comentadas.
- Caché en memoria simple con TTL donde haga falta (sin Redis ni dependencias externas de caché).

---

## 13. Estructura de proyecto

```
tikdown-rs/
├── LICENSE                     # MIT por defecto, ver §0.1 [Añadido en el análisis posterior]
├── .env.example
├── .gitignore
├── .dockerignore
├── .python-version            # fija el minor de Python (3.13) para uv
├── .pre-commit-config.yaml    # ruff check + format (F-22)
├── README.md                  # incluye: qué NO commitear, disclaimer legal, nota naming "-rs",
│                              #   backup de fernet.key, procedimiento de recuperación (§23.5.2),
│                              #   limitaciones T23/T66
├── pyproject.toml             # incluye [project.scripts] tikdown-rs = "cli.main:run" (lección L-A2, §21)
├── uv.lock
├── alembic.ini                # localizado por candidatos en runtime (T70); logger alembic a WARNING
├── Dockerfile                 # en la raíz (patrón F-03/F-04, §11)
├── docker-compose.yml         # en la raíz (contexto de build ., env_file .env)
├── alembic/
│   ├── env.py                 # template async (run_sync) — el default síncrono no sirve con aiosqlite (T51)
│   ├── script.mako
│   └── versions/
├── core/
│   ├── config.py              # Settings (pydantic-settings) + validate_for_daemon()
│   ├── paths.py               # videos_root(), default_outtmpl() — todo deriva de DATA_DIR
│   ├── db.py                  # WAL, PRAGMAs (busy_timeout PRIMERO, L-C5), listener de contención (§5.8), NullPool
│   ├── tasks.py               # create_supervised_task(), cancel_pending_tasks()
│   ├── backoff.py
│   ├── errors.py              # clasificación de 3 estados (§4.3)
│   ├── verify.py              # selfcheck de impersonación, 3 causas (§4.1)
│   ├── archive.py             # dedupe + descarte tolerante (best-effort); parser de ambos formatos de línea (L-C8)
│   ├── download_engine.py     # Protocol + semáforo (por proceso) + cooldown cross-proceso vía SQLite (§4.5)
│   ├── network_monitor.py
│   ├── daemon_state.py        # singleton con ON CONFLICT DO NOTHING; helpers que commitean internamente (T37)
│   ├── migrations.py          # idempotentes (T29) + lock (T68) + _find_alembic_ini() (T70)
│   ├── crypto.py              # Fernet + permisos 0600 + generación atómica O_EXCL (T67) + rama de archivo vacío
│   ├── cookie_parser.py       # parse Netscape/JSON/cookie-string + NETSCAPE_HEADER compartido (T73)
│   ├── ytdlp_updates.py       # check de versión vía GitHub API con ETag (T4)
│   └── notifications/
│       ├── base.py            # servicio Noop + contrato
│       ├── events.py          # catálogo + plantillas + paridad plantilla↔productor (T34, F-08)
│       ├── telegram.py        # ExtBot + spool (T42, F-06) + coalescing + clip() (F-07)
│       └── telegram_bot.py    # Application PTB, auth, throttle, botones (T38), uploads
├── services/
│   ├── accounts.py
│   ├── cookies.py             # validación 3 estados, sonda (T57/T74), _mkstemp_netscape (T73)
│   ├── backfill.py            # slot, cursor, --then-monitor, retry-failed
│   ├── monitor.py
│   ├── videos.py              # handle_download_result centralizado (§4.6)
│   ├── stats.py
│   └── breaker.py
├── cli/
│   ├── main.py                # callback global --version (invoke_without_command)
│   ├── common.py              # wrappers sync sobre asyncio.run(), run_or_exit, migraciones + Settings por invocación
│   ├── daemon.py
│   ├── monitor.py
│   ├── accounts.py
│   ├── backfill.py
│   ├── cookies.py
│   ├── videos.py
│   └── system.py
├── daemon/
│   ├── run.py                 # arranque (§5.1), apagado (§5.2), reaplicación de logging (T72);
│                              #   el ciclo de vida en UN solo asyncio.run (L-B1)
│   └── jobs.py                # jobs del scheduler; recogida de queued con slot + propagación on_event (T75)
├── models/
│   └── models.py
├── tests/
├── .woodpecker.yml              # CI: ruff, pytest, cobertura, build + smoke docker de la imagen (F-22);
│                              #   + job programado semanal pip-audit/trivy (T76, §1.3) [Añadido en el análisis posterior]
└── docs/
    └── PLAN-MAESTRO-TIKDOWN-RS.md  # este documento: especificación canónica autocontenida
```

**Regla de dependencias**: `services/*` no importa `cli/`, `daemon/` ni `yt_dlp`; el bot no importa `cli/` (una consulta como "últimos N vídeos" vive en `services/videos.query_last_videos`, reutilizada por ambos).

**Nota sobre la documentación**: la carpeta `docs/` contiene **un único documento normativo** (este plan maestro). El conocimiento acumulado (trampas, decisiones, lecciones) vive integrado en las secciones §19–§21 de este documento. Los artefactos `Dockerfile` y `docker-compose.yml` son parte de la estructura raíz del proyecto y se documentan en §11 y §23.

---

## 14. Pruebas y calidad

- Nunca tocar TikTok real: mocks completos de `yt_dlp.YoutubeDL`; SQLite `:memory:` + `StaticPool`; `Settings` aisladas por test (`Settings(_env_file=None, data_dir=tmp)`); verificación explícita de que el `data_dir` por defecto **nunca** se crea durante la ejecución completa de la suite.
- **Nunca consultar el ENTORNO REAL en tests (trampa T69)**: todo valor del entorno (disco, red, reloj) se inyecta o se mochea con valores controlados (`monkeypatch.setattr("daemon.jobs.shutil.disk_usage", lambda _p: _disk(5.0))`). Un test que consulta `shutil.disk_usage()` real depende de dónde caiga `tmp_path` (en Linux `/tmp` es tmpfs con ~99.9% libre; en Windows el disco del usuario) — el mismo test pasa en un entorno y falla en otro (F.I.R.S.T. violado; falló en producción Debian 13 en la implementación anterior).
- Cobertura objetivo: los puntos calientes (motor de descarga, cookies, backfill, monitor) por encima del 85%; los handlers del bot, al ser delgados sobre `services/*`, pueden quedar razonablemente por debajo sin que sea señal de un problema real.
- **Casos obligatorios** (cada uno corresponde a una trampa real de esta especificación):
  - 3 estados de cookie (`valid`/`invalid`/`inconclusive`), incluyendo el caso "sin entries" → `inconclusive`; `last_validated_at` solo se actualiza con `valid`/`invalid` (F-16).
  - Import de cookies con borrado de fuente fallido → sigue reportándose éxito (best-effort); `--keep-source` conserva el archivo (F-15).
  - Countdown de expiración: caso futuro y caso pasado.
  - Secuencia completa solo-audio → descarte del archive → retry con formato mejorado → éxito o fallo de integridad final.
  - Anti-bot transitorio → backoff → éxito eventual.
  - Descarte del archive antes de cada reintento (no solo el primero).
  - Cooldown global: sorteos siempre dentro de [MIN, MAX] con RNG inyectable, `MIN=MAX` → fijo, ambos `0` → desactivado (T62). Los tests del motor desactivan el cooldown por defecto (0,0) para no dormir 30-120s por descarga; los tests de cooldown pasan valores explícitos (L-D3, §21).
  - Selfcheck de impersonación con las 3 causas distinguidas.
  - **Render de la barra de progreso con datos simulados completos**, no solo construcción del objeto (T3).
  - Listener de contención SQLite incrementando el contador correcto (T19).
  - Cancelación de backfill por cambio de estado detectado en base de datos durante la ejecución (T21), incluido: UPDATE condicional de progreso que no resucita un `cancelled` (L-F5/L-F6).
  - Rutas de vídeos, DB, clave y archive todas derivadas de `DATA_DIR` (T8; asserts independientes de plataforma — `Path`, no cadenas con `/` fijo).
  - Migraciones idempotentes: comprobación de `alembic_version` antes de decidir `stamp` vs `upgrade` (T29); localización de `alembic.ini` por candidatos (T70: simular wheel con `__file__` en site-packages + cwd con alembic.ini; y error claro si ninguno existe); lock de migraciones (T68).
  - Paridad plantilla↔productor de eventos de notificación (T34), con el scan **excluyendo `events.py`** (F-08).
  - Clasificación de los literales reales del extractor: `requiring login`, `Log into an account`, `log in for access`, `IP address is blocked`, `status code 0` → transitorio, `keeps sending the same page` → transitorio, `does not have any videos posted` → informativo (T52/T53/T54).
  - Slideshow (extracción sin formatos de vídeo) → `status='skipped'`, sin reintento ni categoría de fallo (T55).
  - Plantillas de notificación con contenido hostil (`_ * <b> &`) escapadas correctamente en HTML (T40); `clip()` exacto al límite de 4096 con sufijo dentro (T39/F-07); spool guarda el evento original y el drenaje re-renderiza (F-06); el render de `username` no duplica el `@` de la plantilla (L-H7).
  - Spool: evento emitido durante `offline` se persiste y se entrega, marcado como diferido, al volver `online` (T42).
  - Sonda de validación rota (perfil inexistente) → ciclo `inconclusive` global sin tocar estados + evento `cookie.validation_probe_failed` (T57); sonda por defecto = perfil verificado (`@rosary657`, §7); sonda que itera las primeras 5 entradas: primera entrada slideshow + segunda con vídeo → `valid`; todas slideshow → `inconclusive` (T74/L-E4); entrada sin URL → se salta; lista de sondas (R12): la segunda candidata sana salva el ciclo aunque la primera esté rota, y `cookie.validation_probe_failed` solo se emite cuando TODAS las candidatas fallan.
  - `get_working_cookie` con revalidación `inconclusive` → NO rechaza la cookie (L-E3).
  - Techo de reintentos transitorios: al alcanzar `MAX_VIDEO_RETRY_COUNT` → `failed`/`transient` + `download.retry_exhausted` (T58).
  - Transición history→monitor: solo desde `backfill_status='completed'` (nunca desde `failed`/`cancelled`); bandera consumida; reconciliación en arranque tras crash (T59); la transición no activa el monitor global (T60).
  - Test de arquitectura: `services/*` no importa `yt_dlp`, `typer` ni el SDK del bot (regla de §13, verificada mecánicamente en CI).
  - Presupuesto de tiempo total: agotar `MAX_VIDEO_TOTAL_TIME_SECONDS` → `failed`/`transient` + `download.retry_exhausted` (T63).
  - Fallo de red a mitad de descarga: no incrementa `retry_count` ni consume el presupuesto de tiempo (T64).
  - Job de disco: espacio libre bajo umbral → `monitor.disk_warning`; ENOSPC → `downloads_paused=1` y reanudación automática al recuperar espacio (T45/T65), con disco mockeado con % controlado (T69).
  - Reintento tras timeout: escribe a ruta `.retry-N` distinta y solo renombra a la definitiva tras verificar integridad (T66).
  - Generación concurrente de `fernet.key` por dos procesos → una única clave final; el perdedor relee la existente (T67); carga con archivo vacío en la ventana de creación → relee hasta que el creador escriba (L-E2, test determinista con hilo que crea el archivo vacío vía `open('xb')` y escribe tras un margen).
  - Coalescing de `download.completed` en ráfaga → un único mensaje resumen, nunca N mensajes; condición `>=` umbral con bandera consumible (§8).
  - El ciclo del monitor salta cuentas con `last_check_at` < 30s (§4.9) pero NUNCA salta cuentas con `last_check_at` NULL (L-G1); `backfill_done` cuenta `skipped` (§10); `upload_date` ausente → fallback al cursor anterior **actualizado** (§4.9, L-F2).
  - Bot: comando desde el `chat_id` permitido pero con `from_user.id` no autorizado → rechazado + `bot.unauthorized_attempt` (§6.3); callbacks también throttleados (F-18); `/check` sin doble `@` (L-H6); guard tolerante a updates sin chat (F-18).
  - Backfill descarga con working cookie; sin cookies → aborta con `backfill.no_cookies` (F-01); `backfill_total` persistido al iniciar cada pasada (F-09); `CancelledError` → `queued` + reconciliación (F-10); recogida respeta el slot (F-10) y propaga `on_event` — con **wrapper SÍNCRONO que captura los kwargs en la creación** (T75: el cuerpo de un fake async no corre hasta el primer await y un stub que cierra corrutinas no lo detecta).
  - Backfill propaga `notify_on_download` de la cuenta a `handle_download_result` (L-G3); monitor propaga el canal SÍNCRONO `on_event` sin envolverlo en async (L-G2 — test que verifica que `download.completed` llega al canal).
  - **Cookies regeneradas aceptadas por el parser REAL**: cargar el tempfile regenerado con el `YoutubeDLCookieJar` real (carga local, sin red) — un parser propio tolerante enmascara el rechazo de la librería (T73).
  - **Integración multi-proceso con SQLite en fichero (WAL real)**: la coordinación CLI↔daemon entera depende de SQLite WAL entre procesos, y `:memory:` + `StaticPool` no la ejercita. Mínimo: (a) `daemon stop` escrito por un proceso y detectado por otro vía heartbeat; (b) dos procesos reservando cooldown concurrentemente sobre `download_pacing_state` — los huecos reservados nunca se solapan (T22), incluido el caso de la fila singleton creada con commit (L-C6); (c) contención bajo escrituras concurrentes con `busy_timeout` sin errores no manejados. Los scripts de subproceso usan tipos correctos (`Path` para `data_dir`), ejecutan las migraciones antes de tocar la base, y se lanzan con `asyncio.to_thread` si el test es async (lección L-L6, §21).
  - **Ciclo de vida del daemon en un solo loop**: el watcher de `stop_requested` detecta la señal y el apagado se completa dentro del mismo `asyncio.run` (L-B1).
  - Rotación de impersonación usa objetos `ImpersonateTarget` reales, no strings (L-D1).
- `ruff check && ruff format` en CI y pre-commit (`.pre-commit-config.yaml` materializado, F-22); suite de pytest en CI; build de imagen Docker + **smoke `docker run --rm ... tikdown-rs --version`** en CI (F-22: el smoke detecta problemas de imagen que el build solo no ve — en la implementación anterior el fallo de la imagen publicable T70 pasó desapercibido porque el `--version` no ejecuta migraciones; el smoke es condición necesaria pero no suficiente: cualquier comando que toque la base de datos debe probarse en la imagen).
- **Tests F.I.R.S.T.**: sin `time.sleep` físico (inyectar un reloj monotónico controlable en los componentes con backoff/cooldown); sin asserts que dependan de la versión exacta instalada de yt-dlp; **los tests nunca escriben fuera de su `tmp_path`** (lección L-L1, §21); sin `asyncio.run()` dentro de tests ya async de pytest-asyncio (L-B2); datos de test independientes del reloj del día (fechas futuras calculadas, no epochs fijos — L-L2).

---

## 15. Tabla consolidada de intervalos y timeouts

| Concepto | Valor | Dónde |
|---|---|---|
| Timeout por vídeo | 10 min | `DownloadEngine` |
| Apagado: tiempo para descargas/jobs en curso | 10 s | `daemon run` |
| Heartbeat | `HEARTBEAT_INTERVAL_SECONDS` (default 10 s) | `daemon/run.py` |
| Probe de red | `NETWORK_PROBE_INTERVAL_SECONDS` (default 30 s; HEAD con timeout `NETWORK_PROBE_TIMEOUT_SECONDS`, default 5s — cableado desde Settings, F-13) | `network_monitor` |
| Umbral offline | 2 ciclos fallidos consecutivos | `network_monitor` |
| Backoff del probe mientras offline | 30 s → techo 120 s (job re-programado tras cada ciclo, F-13) | `network_monitor` |
| Throttle "check" manual por cuenta | 30 s | `services/monitor` |
| Long polling Telegram | 25 s | `telegram_bot` |
| Throttle comandos del bot | 2 s / chat (también callbacks) | Dispatcher |
| Backoff genérico (techo) | 30 min + jitter | `backoff.py` |
| Backoff anti-bot (base → techo) | 10 s → 120 s | `backoff.py` |
| Cooldown global entre descargas | Aleatorio uniforme 30–120 s por descarga (`MIN=MAX` = fijo, `0` = off), reloj compartido cross-proceso | `download_engine` + `download_pacing_state` (T22/T62) |
| Pausa larga backfill | 60-120 s cada ~50 vídeos | `backfill` |
| Expiración de botones inline | 60 s (validada, no solo visual) | Bot dispatcher |
| Validación periódica de cookies | 6 h (30-60s entre sondas, §7) | Job APScheduler |
| Refresco de perfil de cuenta | 48 h | Job APScheduler |
| Comprobación de nueva versión de yt-dlp | 24 h | Job APScheduler (API de GitHub, no PyPI; requests condicionales ETag/`If-None-Match` — un 304 no consume cuota) |
| Intervalo del ciclo del monitor | `MONITOR_INTERVAL_MINUTES` (default 5 min) | `services/monitor.py` |
| Frescura de heartbeat (healthcheck) | ≤ 3 × `HEARTBEAT_INTERVAL_SECONDS` (valor configurado) | `daemon healthcheck` (T50) |
| Pacing interno de extracción | 1-3 s entre requests (`sleep_interval_requests`) | Engine (T56) |
| Drenaje del spool de notificaciones | En transición a `online` y en arranque del daemon | `network_monitor` + envío (T42) |
| Chequeo de disco | 15-30 min (umbral `DISK_WARNING_FREE_PERCENT`) | Job APScheduler (T45/T65) |
| Selfcheck periódico | 24 h (resultado persistido en `daemon_state`) | Job APScheduler (§5.1) |
| Presupuesto de tiempo por vídeo | 900 s (`MAX_VIDEO_TOTAL_TIME_SECONDS`) | `DownloadEngine` (T63) |
| Recogida de backfills `queued` | Job ligero del scheduler, solo con slot libre | `services/backfill` (§10) |

---

## 16. Apéndice: notas de implementación clave

- **PRAGMAs recomendados en cada conexión** (aplicar en el evento `connect` de SQLAlchemy, no una sola vez al arrancar). **Orden obligatorio (lección L-C5, §21)**: `busy_timeout` PRIMERO, `journal_mode` DESPUÉS — `journal_mode=WAL` toma un lock exclusivo y, con el `busy_timeout` por defecto (0), dos procesos arrancando a la vez sobre la misma base fallaban con `database is locked` en el propio connect:
  ```sql
  PRAGMA busy_timeout=5000;
  PRAGMA journal_mode=WAL;
  PRAGMA synchronous=NORMAL;
  PRAGMA foreign_keys=ON;
  PRAGMA temp_store=MEMORY;
  PRAGMA cache_size=-65536;
  PRAGMA mmap_size=268435456;   -- 256 MB, reduce syscalls de lectura bajo backfill+monitor concurrentes
  ```
- Usar `poolclass=NullPool` en `create_async_engine` (evita problemas de SQLite con threads/event loop) — **excepto** en tests, donde se usa `StaticPool` sobre `:memory:` (§14).
- **Sesiones cortas y explícitas (obligatorio)**: toda función de `services/*` abre la sesión, hace el trabajo estrictamente necesario y la cierra lo antes posible (`async with session_factory() as session: ...`). Prohibido mantener una sesión abierta mientras se espera a `yt-dlp` o a una llamada de red (ver también T32 en §7). **Las consultas se ejecutan sobre la sesión, nunca sobre el `async_sessionmaker`** (lección L-C3, §21: `sessionmaker` no tiene `.execute()`), y **dentro de la sesión activa** — llamar a un helper que abre OTRA sesión devuelve objetos detached cuyas mutaciones no persiste el commit exterior (lección L-C4, §21).
- **Relaciones lazy prohibidas fuera de sesión (lecciones L-C1/L-C2, §21)**: en SQLAlchemy async, acceder a una relación lazy (`account.videos`, `video.account`) fuera de la sesión lanza `MissingGreenlet`/`DetachedInstanceError` — cargar explícitamente con `selectinload(...)` en la consulta (esto aplica también a las consultas que reutilizan CLI y bot, p. ej. `query_last_videos`).
- **Trampa T37**: los helpers mutadores de `daemon_state` deben **commitear internamente**, no depender de que el llamador haga el commit tras salir del `async with` — una sesión corta hace rollback silencioso al salir si el commit se olvida en el punto de llamada, lo cual en la práctica hizo que `stop_requested` se perdiera y el daemon no se apagara pese a que el comando `daemon stop` "funcionó" sin error visible. El patrón "commit responsabilidad del llamador" es sistemáticamente propenso a este bug para el estado del daemon; centralizar el commit dentro del propio helper mutador lo elimina de raíz.
- `create_engine`/`create_async_engine` debe crear el directorio padre del archivo de base de datos si no existe — SQLite no crea directorios automáticamente, y un `DATA_DIR` recién montado sin ese directorio produce "unable to open database file" en el primer arranque. El chequeo de URL de memoria debe ser **estructural** (`"///" not in url`), nunca el literal `:memory:` (lección L-C9, §21).
- **Contador de contención de SQLite**: ver §5.8.
- Jobstore de APScheduler: en memoria, por diseño (§5.4), no en base de datos separada.
- **Instanciación perezosa del motor de descarga**: los servicios que no necesitan descargar (p. ej. simplemente añadir una cuenta) no deben forzar la creación de un `DownloadEngine` ni ejecutar el selfcheck de impersonación como efecto secundario de su inicialización — solo instanciarlo cuando la operación concreta lo requiera.
- **Consistencia de versión de yt-dlp**: en entornos de desarrollo con instalación editable es común que la versión reportada por el gestor de paquetes no coincida exactamente con la versión interna reportada por el propio módulo (`yt_dlp.version.__version__`); tratar esto como un warning informativo, no como una condición de fallo del daemon (relacionado con la trampa T4 de §1).

---

## 17. Acceso opcional a archivos por red (WebDAV / media server)

Funcionalidad completamente opcional, desactivada por defecto y fuera del núcleo. El núcleo de TikDown-rs funciona sin ella, y **la aplicación no implementa servidor WebDAV ni consume variables `WEBDAV_*`** (F-17: se retiraron de `Settings` porque el sidecar lee su propio entorno; véase §12).

### 17.1 Recomendación principal: media server

La forma recomendada de consumir los vídeos desde otros dispositivos es apuntar una biblioteca de **Jellyfin** (o Plex) a la carpeta `<DATA_DIR>/videos/{username}/` que genera TikDown-rs: interfaz moderna, miniaturas, metadatos, transcodificación, reanudación de reproducción, buen soporte en TV/móvil. Es la solución más cómoda a largo plazo en un homelab.

### 17.2 Alternativa ligera: WebDAV vía rclone

Si se prefiere acceso tipo "carpeta de red" sin un media server completo, usar `rclone serve webdav` (binario único, ligero, buen soporte de autenticación) como sidecar en `docker-compose.override.yml` (o contenedor aparte). La raíz servida debe coincidir exactamente con la raíz real de vídeos derivada de `DATA_DIR` (T8) — cualquier discrepancia deja el WebDAV apuntando a una carpeta vacía o equivocada. La plantilla del sidecar y sus reglas están en §23.4.

**Seguridad**: solo lectura siempre; autenticación siempre obligatoria; idealmente detrás de reverse proxy con HTTPS; nunca exponer directamente a internet sin VPN (Tailscale/WireGuard) + autenticación fuerte, dado que el contenido puede ser sensible indirectamente. Las credenciales WebDAV siguen las mismas reglas de §0.1: nunca en el repositorio, solo en el `.env` real de despliegue.

---

## 18. Backlog futuro (no bloquea el MVP)

Una vez el MVP funcional descrito arriba esté completo, probado (tests unitarios en verde, `ruff check` limpio, selfcheck exitoso, al menos un backfill real de prueba completado) y desplegado, estas mejoras son candidatas razonables para iteraciones posteriores, en orden aproximado de valor. *(Nota: los puntos históricos "rotación round-robin de proxies" y "backup consistente de la base de datos" están **implementados en el MVP** — decisión F-21b — y ya no son backlog; el backup vive en `system backup`, la rotación en §4.7.)*

1. Paginación real en `/list` del bot (botones/callback con offset), en vez del límite fijo con aviso del MVP.
2. `DownloadArchive` con I/O explícitamente movido a `to_thread` y una estrategia de tombstones/compactación para archivos de deduplicación muy grandes (hoy: uso single-process documentado como límite conocido).
3. Recogida automática de backfills pausados por caída de red + slot de backfill como barrera cross-proceso real (hoy: recogida de `queued` por el daemon y pacing compartido cross-proceso vía SQLite, §4.5 y §10; el slot sigue siendo por proceso; si se implementa la pausa, reintroducir el evento `backfill.paused`).
4. Tipado más estricto en contratos críticos (`async_sessionmaker[AsyncSession]` explícito, `Protocol` para las dependencias del bot) — reemplazar usos de `Any` donde hoy existen.
5. Métricas y `healthcheck` más ricos: no solo heartbeat fresco, también estado de cookies, disco, últimos errores, contador de contención SQLite expuesto de forma más detallada.
6. Logs a archivo rotado como alternativa a JSON-a-stdout puro en el daemon.
7. Estado de red visible de forma fiable desde `daemon status` en cualquier proceso (requiere que el daemon persista explícitamente un snapshot de su estado de red, más allá del heartbeat mínimo actual).
8. Tests de concurrencia real: semáforo/cooldown ejercitados con dos instancias de engine en paralelo, cancelación de tareas durante el apagado, escritura concurrente del archive.
9. Rotación activa/clasificación de IP de salida cuando se usa un pool de proxies, para detectar y descartar automáticamente salidas ya bloqueadas por TikTok en vez de esperar a que el circuit breaker las marque una a una.
10. Auto-descubrimiento opcional de listas de proxies de terceros, si el volumen de cuentas monitorizadas lo justifica — mantener desactivado por defecto y documentar los riesgos de confiar en listas de proxies no verificadas.
11. Archivado opcional de slideshows como audio + carátula (hoy: `status='skipped'`, §4.6).
12. Migración de volumen/host: futuro comando de reparación de rutas para `local_path` absoluto (§2) — no existe en el MVP, §3 — o almacenamiento de ruta relativa a `DATA_DIR` resuelta en runtime; decisión diferida, el MVP documenta la limitación.
13. Supervisión del bot de Telegram: error handler (`add_error_handler`) que detecte la muerte del polling (`Conflict` 409 por otra sesión de `getUpdates` u otro error definitivo) y emita evento + reinicio automático del bot sin reiniciar el daemon (hoy: limitación documentada, T71 — la recuperación es reiniciar el contenedor). **Advertencia verificada externamente (PTB issue #3430)**: en PTB ≥20 los errores del `get_updates` pueden NO llegar al `add_error_handler` como en v13 — verificar empíricamente la vía de detección contra la versión pineada antes de implementar.

---

## 19. Trampas operativas consolidadas (checklist del implementador)

Cada fila corresponde a un bug real conocido en este tipo de sistema (diagnosticados en la implementación anterior del proyecto — ver §21). El implementador debe verificar explícitamente cada una durante el desarrollo, no solo leerlas de pasada.

| # | Trampa | Mitigación |
|---|---|---|
| T1 | `add_done_callback` con función async nunca se ejecuta | Callback **síncrono** que audita con `task.exception()` |
| T2 | `prerelease="allow"` global mete alphas de otros paquetes en el lock (también transitivas — verificado en la doc oficial de uv) | `prerelease-package = { "yt-dlp" = "allow" }`, nunca global |
| T3 | `rich.progress`: acceso con `{task.fields.clave}` → error; `total`/`completed` mutan la barra real | `{task.fields[clave]}` + nombres propios no colisionantes + test de render con datos simulados |
| T4 | El nightly en PyPI se normaliza (PEP 440); la versión interna del módulo coincide con el tag de GitHub | Comparar siempre contra `yt_dlp.version.__version__`, nunca contra la versión del gestor de paquetes |
| T5 | 403 genérico tratado como definitivo → pausa cuentas sanas vía circuit breaker | 403 sin hints de auth = **transitorio**; orden de evaluación estricto en `classify_error` |
| T6 | Serie de `curl-cffi` incompatible con yt-dlp, o betas coladas por prerelease global → targets `(unavailable)` | Pin **exacto**, no rango (preferentemente el extra `pin-curl-cffi` de yt-dlp); selfcheck que distingue las 3 causas |
| T7 | `fernet.key` existente con permisos amplios (`0644`) deja cookies descifrables por otros usuarios | Verificar/corregir a `0600` también sobre la clave existente al cargarla; generación atómica con `O_EXCL` (T67) |
| T8 | Rutas relativas de vídeos → fuera del volumen en Docker, fallos de permisos con working directory equivocado | Todo deriva de `DATA_DIR` vía `core/paths.py` |
| T9 | Con `AsyncIOScheduler`, `shutdown(wait=True)` **no espera** a los jobs en curso: `AsyncIOExecutor` cancela los futures pendientes y, llamado desde dentro del loop, el cierre retorna de inmediato (`call_soon_threadsafe`) — un drenaje confiado a `wait=True` es ficticio | Pausar/detener el scheduling como señal + drenaje real vía el registro de tareas supervisadas (T27/T28) antes de disponer recursos |
| T10 | `run_polling()` dentro de un loop existente → "event loop already running" | `initialize() → start() → updater.start_polling()` (secuencia oficial PTB) |
| T11 | `wait_for(lock.acquire(), timeout=0)` siempre lanza `TimeoutError` (verificado en CPython ≥3.12: caso especial para `timeout <= 0` que cancela la corrutina sin ejecutarla, incluso con el lock libre) | `if lock.locked(): return False; await lock.acquire()` |
| T12 | `ffprobe`/SHA-256/`fsync` bloquean el event loop por ser "cortos" pero síncronos | `asyncio.to_thread` para toda I/O pesada, no solo llamadas de red |
| T13 | Nombre de archivo que empieza con `-` se lee como opción de `ffprobe` | `--` antes de la ruta del archivo |
| T14 | Un fallo de limpieza posterior a un éxito aborta el procesamiento completo | Best-effort con warning (`safe_discard` o equivalente) |
| T15 | `COPY . .` sin `.dockerignore` embebe secretos en capas Docker recuperables | `.dockerignore` completo desde el primer commit; **re-incluir `README.md`** (F-04, hatchling) |
| T16 | Selfcheck que no valida crypto deja pasar una clave rotada sin avisar | Selfcheck descifra una cookie real; distingue "tabla ausente" de error real |
| T17 | Singleton `daemon_state` sin `ON CONFLICT` → carrera en el primer arranque concurrente | `INSERT ... ON CONFLICT DO NOTHING` + relectura |
| T18 | `typer` sin soporte async nativo (confirmado: sin merge upstream del auto-wrap) | Wrappers síncronos con `asyncio.run()` centralizados en `cli/common.py` |
| T19 | Contador de contención leído desde el proceso CLI siempre da 0 | Listener `handle_error` + persistencia en heartbeat + lectura siempre desde `daemon_state` |
| T20 | `accounts check` con motor/clave simulados "comprobaba" sin hacer nada y reportaba éxito | Motor y clave **reales**, tanto en CLI como en el bot |
| T21 | `backfill cancel` que solo escribe en DB pero no detiene el proceso en curso | `run_backfill` relee el estado periódicamente durante la ejecución + UPDATE condicional de progreso (L-F5) + retorno temprano del outcome (L-F6) |
| T22 | Cooldown por instancia de engine o solo en memoria del proceso → sin pacing global real: daemon + CLI descargan en paralelo desde la misma IP | Reloj de pacing persistido en SQLite (`download_pacing_state`) + `reserve()` atómico cross-proceso (`UPDATE ... RETURNING`); **commit tras el INSERT del singleton** (L-C6) + timestamps con milisegundos (L-C7) |
| T23 | `wait_for` con timeout no mata el hilo nativo de yt-dlp | Documentar la limitación; confiar en integridad post-descarga + descarte de parciales |
| T24 | Reintento con formato mejorado sin descartar el archive antes → yt-dlp ve "already downloaded" | Descarte del archive **antes** del segundo intento, siempre |
| T25 | Configuración inválida deja el daemon arrancado a medias | `validate_for_daemon()` como primer paso del arranque |
| T26 | El bot crea un engine nuevo por cada comando recibido | Dependencias inyectadas por el daemon + flag `owns_engine` |
| T27 | Jobs de APScheduler activos quedan fuera del drenaje de tareas en el apagado | Los jobs de ciclo largo lanzan su trabajo como tareas supervisadas; el apagado drena ese registro (T28), no el scheduler (T9) |
| T28 | Registro de tareas supervisadas sin referencias `Task` reales → no se pueden cancelar de verdad | Guardar `_task_refs` reales + `cancel_pending_tasks()` |
| T29 | Lógica de migración repite `stamp` en cada comando por no comprobar `alembic_version` primero | Comprobar la tabla `alembic_version` antes de decidir `stamp` vs `upgrade` |
| T30 | Índice de tareas supervisadas por nombre → colisión entre tareas con el mismo nombre lógico | Indexar por `id(task)` |
| T31 | Tempfile huérfano si la escritura falla a medias | Ruta asignada justo tras `mkstemp` + `os.close(fd)` inmediato (L-H5) + limpieza garantizada en el propio worker |
| T32 | Sesión SQLite mantenida abierta durante una llamada de red | Patrón: leer blob → cerrar sesión → validar → reabrir para persistir |
| T33 | Fecha de expiración de cookie corrupta/absurda → `OverflowError` en `datetime.fromtimestamp` | Clamp a una fecha máxima razonable (p. ej. `2100-01-01`) antes de convertir |
| T34 | Evento de notificación emitido en el código sin plantilla correspondiente → notificación perdida en silencio | Verificar con tests la paridad plantilla ↔ productor para todo el catálogo (excluyendo `events.py` del scan, F-08) |
| T35 | `network.online` notificado incluso tras un simple "blip" de red de duración 0 | Capturar la duración de la caída antes de limpiar el timestamp; notificar solo desde estado `offline` confirmado |
| T36 | Variable declarada en `.env.example` sin efecto real conectado en el código | Cada variable, o bien conectada de verdad, o bien retirada del ejemplo (caso resuelto: `WEBDAV_*`, F-17) |
| T37 | Mutación de `daemon_state` sin commit explícito en una sesión corta → rollback silencioso al salir | Los helpers mutadores de `daemon_state` commitean internamente, no dependen del llamador |
| T38 | `callback_data` de Telegram limitado a 64 bytes (límite crudo de la API; PTB no añade overhead a payloads `str`): timestamp + acción + payload no caben si se serializa ingenuamente; falla al crear el botón, no al pulsarlo | Encoding compacto presupuestado (acción corta, timestamp epoch, payload acotado); test que construya el callback más largo posible |
| T39 | Mensaje de Telegram >4096 chars → `Message_too_long` y pérdida silenciosa en envío best-effort | Troceado/truncado con indicación; helper `clip()` compartido con el sufijo DENTRO del límite (F-07); los errores definitivos (BadRequest) se descartan sin re-spolear |
| T40 | MarkdownV2 + contenido de TikTok (`_ . ( ) # !`) → `can't parse entities`; notificación perdida en silencio | `parse_mode=HTML` + `html.escape()` en todo contenido dinámico (en `event_message` Y en los handlers del bot, F-05); degradación a texto plano ante `can't parse entities`; MarkdownV2 prohibido |
| T41 | Ráfaga de notificaciones (backfill + `notify_on_download`) → 429 de Telegram y pérdida de eventos | `AIORateLimiter(max_retries≥3)` (sus defaults ya son los límites oficiales) en el bot y en el `ExtBot` de notificaciones; **requiere el extra `python-telegram-bot[rate-limiter]`** (L-H2) |
| T42 | `network.offline` y demás eventos emitidos durante la caída se pierden porque el propio envío necesita red | Spool `pending_notifications` en SQLite (solo con notificaciones habilitadas), drenado al volver `online` y en el arranque; entregas marcadas como diferidas; el spool guarda el evento ORIGINAL (F-06) |
| T43 | `upload_date` con formatos mezclados (`YYYYMMDD` vs ISO8601) rompe la comparación lexicográfica del cursor de backfill | Un único formato canónico por columna, normalizado en el borde de ingesta; yt-dlp entrega `YYYYMMDD` (vía `createTime`) — conservarlo |
| T44 | Job de APScheduler más lento que su intervalo → ejecuciones solapadas del mismo ciclo (monitor, validación) | `max_instances=1` + `coalesce=True` en jobs de ciclo largo |
| T45 | Disco lleno (ENOSPC) a mitad de descarga clasificado como error genérico → circuit breaker o reintentos inútiles | Rama propia: fallo local accionable, `downloads_paused=1` en `daemon_state` + alerta Telegram + reanudación automática al recuperar espacio (job de disco, T65); no cuenta para breaker ni toca cookies |
| T46 | Selfcheck sin sonda de `ffmpeg`/`ffprobe` → el binario ausente se descubre en el primer merge fallido | Selfcheck = impersonación + crypto (T16) + ffmpeg/ffprobe ejecutables |
| T47 | Línea parcial al final de `download_archive.txt` tras corte a mitad de `write()` → parser que falla entero | Parser tolerante que salta la última línea malformada; SQLite como fuente de verdad |
| T48 | Re-entrega de updates de Telegram tras reinicio (offset en memoria de PTB) → comandos ejecutados dos veces | Handlers idempotentes por diseño; si se persiste el offset en SQLite, implementarlo explícitamente (PTB no lo hace) |
| T49 | Export CSV con campos que empiezan por `=`, `+`, `-`, `@` (o con espacios/tabs/CR — también `\x0b`/`\x0c` — antes del operador) → inyección de fórmulas al abrir en hojas de cálculo | `csv.writer` de la stdlib (quoting RFC 4180) + sanitización con `lstrip(" \t\r\n\x0b\x0c")` antes del chequeo de operador (F-11, OWASP/CWE-1236) |
| T50 | `healthcheck` sin definición de "heartbeat fresco" → Docker marca healthy un daemon zombi | Frescura = `last_heartbeat_at` ≤ 3 × `HEARTBEAT_INTERVAL_SECONDS` configurado; `--start-period` ≥ duración del selfcheck de arranque |
| T51 | `env.py` de Alembic síncrono con driver aiosqlite → migraciones que fallan en arranque | Template async / `connection.run_sync` desde el primer commit |
| T52 | Marcadores de auth literales ("login required") que no casan con los mensajes reales de yt-dlp ("requiring login", "Log into an account") → fallos de auth clasificados como transitorios | Lista de marcadores derivada de los literales del extractor (§4.3); matching case-insensitive sobre la cadena completa de la excepción |
| T53 | `Video not available, status code 0` tratado como contenido inexistente cuando es la firma de respuesta degradada/anti-bot | `status code 0` → transitorio con agotamiento por reintentos; definitivo solo códigos no-cero explícitos |
| T54 | `TikTok API keeps sending the same page` tratado como fallo de cuenta | Transitorio (`expected=True` upstream); el extractor reintenta con otro `device_id` |
| T55 | Slideshow (audio-only **esperado**) clasificado como fallo de integridad y reintentado en bucle para siempre | `expected_has_video` del `info_dict`: sin formatos de vídeo en la extracción → `status='skipped'`, sin reintentos (§4.6) |
| T56 | Enumeración de feeds multi-página sin pacing interno → ráfaga de requests API que dispara el WAF de TikTok | `sleep_interval_requests` 1-3s + `extractor_retries` 5-10 en el engine (§4.2) |
| T57 | Sonda de validación rota (perfil eliminado/renombrado) → todas las cookies marcadas inválidas a la vez | Sonda rota ⇒ ciclo `inconclusive` global sin tocar estados + `cookie.validation_probe_failed`; "todas inválidas simultáneas" es sospecha de sonda/red, nunca invalidez masiva (§7) |
| T58 | Fallo transitorio persistente reintentado indefinidamente → vídeo en bucle invisible que nunca sale como fallido | `MAX_VIDEO_RETRY_COUNT` (default 5) **y** `MAX_VIDEO_TOTAL_TIME_SECONDS` (default 900s, T63) → `failed`/`transient` + `download.retry_exhausted`; recuperable vía `retry-failed`; los fallos de red no consumen reintentos (T64) |
| T59 | Transición history→monitor escrita por separado del completado del backfill → ventana de pérdida en crash | Misma transacción con condición idempotente + reconciliación defensiva en arranque (§5.1) |
| T60 | Auto-transición que arranca el monitor global como efecto lateral, violando la decisión de seguridad de §5.1 | La transición solo cambia `mode`; el arranque global sigue siendo manual/`MONITOR_AUTOSTART` |
| T61 | Notificación de descarga sin contexto accionable (sin enlace, categoría ni motivo) → el usuario debe rebuscar en logs para saber qué pasó | Plantilla obligatoria: fallos = URL de TikTok + categoría + motivo + siguiente paso; éxitos = URL de TikTok + ruta local + duración/resolución/codecs verificados (§8) |
| T62 | Cooldown fijo entre descargas → patrón de peticiones en metrónomo, fingerprinteable por el anti-bot | Sorteo uniforme en [MIN, MAX] por descarga, dentro del `reserve()` atómico (T22); RNG inyectable para tests |
| T63 | Reintentos inline sin presupuesto de tiempo total: con semáforo global = 1, un solo vídeo (5 intentos × 10 min + backoffs) bloquea todas las descargas >1h (head-of-line blocking) | `MAX_VIDEO_TOTAL_TIME_SECONDS` (default 900s) agregado por vídeo; al agotarse → mismo destino que T58 |
| T64 | Una caída de red a mitad de descarga consume `retry_count` y acaba marcando `failed` un vídeo sano por un problema ajeno | Fallos de red (probe `offline` / `network_available` en clear) no incrementan `retry_count` ni consumen presupuesto de tiempo (§4.4, §9) |
| T65 | Evento del catálogo sin productor real (`monitor.disk_warning` sin job de disco) → nunca se emite aunque el código "cumpla" su parte | Job de chequeo de disco (15-30 min, umbral `DISK_WARNING_FREE_PERCENT`) en §5.1 como productor; paridad plantilla↔productor verificada con tests (T34) |
| T66 | El hilo zombi de yt-dlp tras un timeout (T23) sigue escribiendo el mismo `outtmpl` que el reintento posterior → carrera de escritura sobre el archivo | Reintentos a ruta temporal distinta (`.retry-N`) renombrada solo tras verificar integridad; zombis registrados y expuestos en `daemon status` |
| T67 | Dos procesos generan `fernet.key` a la vez en el primer arranque → el último en escribir gana y las cookies cifradas con la otra clave quedan irrecuperables; **y además**: en la ventana entre el `open('xb')` del ganador y su escritura, cualquier lector (incluida la rama directa de carga) encuentra el archivo VACÍO → `ValueError` intermitente (~50% en Linux) | Creación atómica `open(..., 'xb')`/`O_EXCL`; el perdedor relee la clave existente. **Ambas ramas de carga** (perdedor del `O_EXCL` y carga directa) tratan el archivo vacío como ventana de creación: releer con reintentos (50×10ms); solo la corrupción no vacía o el vacío persistente se propagan |
| T68 | Dos procesos ejecutan migraciones Alembic a la vez → carrera sobre `alembic_version` y las DDL | Lock de fichero `<DATA_DIR>/.migrate.lock` alrededor de toda la lógica de §5.5 |
| T69 | Test que consulta el ENTORNO REAL en vez de mockear (p. ej. `shutil.disk_usage()` con umbral asumido): el resultado depende de dónde caiga `tmp_path` — en Linux `tmp_path` está en `/tmp` (tmpfs con ~99.9% libre), en Windows en el disco del usuario → el mismo test pasa en un entorno y falla en otro (F.I.R.S.T. violado) | Todo valor del entorno (disco, red, reloj) se inyecta o se mochea con valores controlados: `monkeypatch.setattr("daemon.jobs.shutil.disk_usage", lambda _p: _disk(5.0))` — umbrales explícitos e independientes del filesystem (ver §14) |
| T70 | Recursos del proyecto (p. ej. `alembic.ini`) localizados con `Path(__file__).resolve().parents[1]` → se rompen en instalación **wheel** (`uv sync --no-editable`, Dockerfile): el módulo vive en site-packages y el wheel solo empaqueta los paquetes de código, no los archivos de configuración de la raíz → `CommandError: No 'script_location' key found` en el arranque del contenedor (el smoke `--version` no ejecuta migraciones y no lo detecta) | Resolver por candidatos con error explícito: (1) junto al módulo (dev editable), (2) cwd (la imagen hace `COPY . .` + `WORKDIR /app`); si ninguno existe, `FileNotFoundError` accionable en vez del error confuso de la librería |
| T71 | Una sesión de `getUpdates` manual de diagnóstico contra un bot en polling → `telegram.error.Conflict: terminated by other getUpdates request` (409): la Bot API solo permite UNA sesión simultánea; el polling del bot muere y el daemon NO lo detecta ni lo reinicia (sigue healthy con el bot muerto en silencio — solo visible en logs) | Nunca llamar a `getUpdates` manualmente contra un bot en polling: verificar con `getMe`/`sendMessage`. Recuperación: reiniciar el contenedor (el orquestador). Limitación del MVP: sin supervisión del polling (backlog §18); verificar empíricamente la vía de detección (PTB #3430) |
| T72 | `fileConfig()` de Alembic (desde `apply_migrations`) RECONFIGURA el root logger con el `[logger_root] level = WARNING` de `alembic.ini` y un handler stderr genérico — aunque `env.py` use `disable_existing_loggers=False` (que solo evita deshabilitar loggers existentes): un daemon que configura logging JSON/INFO ANTES de migrar pierde TODOS sus logs INFO silenciosamente tras la primera migración (docker logs con 0 bytes, daemon healthy; el CLI no lo sufre porque no configura logging) | Reaplicar el logging del daemon (`_setup_logging(level)` con `force=True`) inmediatamente DESPUÉS de `apply_migrations` en el arranque; test de integración que verifica el nivel y el formatter JSON del root tras el ciclo migración→arranque |
| T73 | Un parser propio TOLERANTE enmascara el rechazo del parser REAL de la librería: el import acepta archivos Netscape sin header (y los tests mockean `validate_cookie`), pero `YoutubeDLCookieJar` de yt-dlp usa `MozillaCookieJar._really_load` de CPython, que exige el magic `# Netscape HTTP Cookie File` en la PRIMERA línea — todo archivo regenerado desde el blob cifrado (sin header) era rechazado en producción: `'<tmp>' does not look like a Netscape format cookies file` → TODA validación y uso de cookies roto | Todo archivo Netscape reconstruido lleva el header mágico (constante `NETSCAPE_HEADER`, sin duplicar si el texto ya lo trae) + `newline="\n"` explícito (Windows escribiría CRLF); los tests de cookies verifican el tempfile regenerado con el `YoutubeDLCookieJar` REAL (carga local, sin red) — nunca solo con el parser propio |
| T74 | Sonda de validación con EMBEDDING DESHABILITADO (típico de cuentas oficiales grandes, p. ej. `@tiktok`) o con PRIMERA ENTRADA SLIDESHOW: el extractor avisa *"This user's account is either private or has embedding disabled"* y el primer vídeo del feed devuelve `No video formats found` → la sonda produce falsos `inconclusive` con cookies VÁLIDAS (el sistema no invalida — correcto — pero la sonda queda inútil y el ciclo 6h degrada a `inconclusive` permanente). "No video formats found" es además un fallo conocido del extractor (issues upstream #12441/#11701/#12610), no solo de perfiles con embedding off | Al elegir/reemplazar la sonda: verificar con una extracción REAL (`yt-dlp -s <perfil>` devuelve formatos para su primer vídeo) antes de desplegarla; repetir la verificación al cambiar de nightly; **la sonda itera las primeras `PROBE_MAX_ENTRIES=5` entradas buscando formatos de vídeo** (L-E4); default en código = **`@rosary657`** (verificado en producción, §7); criterios de elección ampliados en §7 |
| T75 | Canal de eventos (`on_event`) NO propagado a la corrutina lanzada por un job (p. ej. `collect_queued_backfills` emitía `backfill.queued` desde el propio job pero llamaba a `run_backfill` sin el canal) → `backfill.completed`/`monitor_activated`/`no_cookies` y todos los `download.*` se perdían SILENCIOSAMENTE en la ruta del daemon (la CLI foreground sí propagaba) — rompe el principio de notificaciones exhaustivas del plan. Invisible en tests: el stub del registry cierra la corrutina sin ejecutarla, y un fake `async` capturando en su cuerpo tampoco lo detecta (el cuerpo no corre hasta el primer `await`) | Todo job que lanza una corrutina con canal de eventos lo PROPAGA explícitamente; test de regresión que captura los kwargs en la CREACIÓN con un wrapper SÍNCRONO devolviendo una corrutina vacía (única forma de ver los argumentos con stubs que no ejecutan) |

---

## 20. Decisiones de diseño consolidadas

> **Qué es esta sección**: el registro del **qué, por qué y con qué evidencia** se tomó cada decisión de fondo del diseño. Proviene del historial de enmiendas y auditorías de la implementación anterior del proyecto (que fue construida, auditada y operada en producción antes de decidir su reconstrucción desde cero); aquí se conserva la decisión vigente y su justificación, no la cronología. Cuando una decisión nació de un error real, la lección detallada está en §21 y la mitigación operativa en §19 (T##). Los hallazgos de la auditoría externa de la implementación anterior se citan como `F-01…F-22`.

### 20.1 Stack y dependencias

- **yt-dlp se pinea contra el canal nightly, nunca el estable** (§1): TikTok rompe su frontend con frecuencia suficiente para que el estable llegue sistemáticamente atrasado frente a los parches de extracción; el nightly los publica el mismo día y recibe antes los parches de seguridad. El nightly se publica como dev release en PyPI; el pin es la fecha exacta (`YYYY.MM.DD.HHMMSS`) porque un nightly es por definición menos probado — la reproducibilidad del build manda. Comparación de versiones siempre con `yt_dlp.version.__version__` (T4).
- **`prerelease` solo para yt-dlp, nunca global** (T2): verificado en la documentación oficial de uv que el modo global aplica también a transitivas — un `pydantic` alpha en el lock es un riesgo de cadena de suministro.
- **`curl-cffi` con pin exacto gestionado por el extra `pin-curl-cffi` de yt-dlp** (T6): yt-dlp mantiene un guard de compatibilidad de versión máxima; el propio upstream fija la versión exacta soportada y la actualiza con cada nightly — preferir ese mecanismo a un pin manual propio (que queda como fallback).
- **`aiosqlite != 0.22.0` y `SQLAlchemy>=2.0.51`** (§1): regresión de hang verificada (omnilib/aiosqlite#369 → sqlalchemy#13039, fix `380c234`).
- **APScheduler 3.x, no 4.x** (§1): la serie 4 sigue en pre-release con advertencia explícita contra producción.
- **Logging stdlib con formatter JSON ad-hoc; `structlog` retirado del stack** (F-20): la auditoría demostró que se declaró sin ningún consumidor (grep vacío). No reintroducir dependencias de logging sin justificación.
- **`python-telegram-bot[rate-limiter]` con el extra explícito** (L-H2): `AIORateLimiter` vive en el extra; sin él el bot no se construye.
- **ffmpeg/ffprobe como dependencia dura con sonda en el selfcheck** (T46): su ausencia solo se descubriría en el primer merge fallido, clasificado como error genérico.

### 20.2 Clasificación de fallos y resiliencia

- **Tres categorías de fallo, nunca dos** (principio 11): definitivo / transitorio / inconcluso (este último específico de cookies). Tratar un transitorio como definitivo es la fuente más común de bloqueos autoinfligidos. Las únicas reclasificaciones hechas tras verificar los literales reales del extractor (§4.3) refuerzan este principio: los marcadores de auth se derivan de los mensajes literales de yt-dlp (T52), `status code 0` es transitorio (T53), `keeps sending the same page` es transitorio `expected` upstream (T54).
- **`status='skipped'` para slideshows** (T55): distinción verificada en el fuente del extractor entre audio-only esperado (post de fotos — todo lo descargable es el audio) y respuesta degradada; el engine lo sabe en el momento de la descarga (`expected_has_video`) y lo comunica a `handle_download_result`.
- **Cooldown aleatorio uniforme [30s, 120s] por descarga, nunca fijo** (T62): un intervalo fijo es un metrónomo fingerprinteable por el anti-bot. Cooldown (pacing, siempre activo) y backoff (reacción a fallos, exponencial con techo) son mecanismos independientes que nunca se sustituyen: el backoff se SUMA al cooldown tras un fallo.
- **Reloj de pacing persistido en SQLite cross-proceso** (T22): daemon y CLI son procesos distintos descargando desde la misma IP; el `reserve()` es un `UPDATE ... RETURNING` atómico. Implementación con commit inmediato del singleton y timestamps de milisegundos (L-C6/L-C7).
- **Techo de reintentos + presupuesto de tiempo total por vídeo** (T58/T63): un transitorio persistente sin techo es un reintento infinito invisible; con semáforo global 1, un solo vídeo podía bloquear todas las descargas >1h. Al agotarse → `failed`/`transient` + `download.retry_exhausted`, recuperable vía `retry-failed`.
- **Los fallos de red no consumen reintentos ni presupuesto** (T64): invalidar trabajo por una caída ajena es el mismo anti-patrón que invalidar una cookie sana por falta de red.
- **Circuit breaker por cuenta solo con fallos de auth reales** (§4.4): 5 consecutivos → `paused + needs_review` + `monitor.account_paused`; los transitorios no cuentan. El contador vive en memoria del proceso y se resetea al reiniciar (limitación documentada: las pausas sí persisten).

### 20.3 Daemon y scheduler

- **Jobstore en memoria, no persistido** (§5.4): `SQLAlchemyJobStore` serializa con pickle y falla con bound methods que referencian al scheduler; todos los jobs son de intervalo simple recreados determinísticamente en cada arranque; el estado que debe sobrevivir ya vive en SQLite de negocio.
- **El drenaje del apagado lo hace el registro de tareas supervisadas, no el scheduler** (T9/T27/T28): verificado en el fuente de APScheduler 3.x que `shutdown(wait=True)` con `AsyncIOExecutor` cancela en vez de esperar; la semántica se mantiene en la serie 4.
- **`daemon.stopped` se emite ANTES del drenaje** (F-19): la tarea de envío entra en `cancel_pending` (10s) con el engine aún vivo; emitirlo tras disponer recursos hacía que casi nunca se entregara.
- **El monitor siempre arranca detenido** (`MONITOR_AUTOSTART=false` por defecto, §5.1): decisión de seguridad — ninguna automatización (incluida la transición `--then-monitor`, T60) activa el monitor global como efecto lateral.
- **El heartbeat es también watcher de control** (§5.1): observa `monitor_running` y `stop_requested` y aplica los cambios en caliente; no hace falta señalización adicional CLI→daemon.
- **`daemon healthcheck` = solo frescura de heartbeat** (T50); el selfcheck corre en arranque y como job periódico 24h con resultado persistido en `daemon_state` — instanciar `YoutubeDL` + abrir la DB + descifrar una cookie cada ~30s sería coste innecesario y superficie de falsos negativos.
- **Migraciones idempotentes desde el día uno** (§5.5): comprobación de `alembic_version` antes de `stamp` vs `upgrade` (T29), lock de fichero entre procesos (T68), localización por candidatos (T70), `env.py` async (T51), logger de alembic a WARNING.
- **Reaplicar el logging tras migrar** (T72): `fileConfig()` de Alembic pisa el root logger aunque `disable_existing_loggers=False`.

### 20.4 yt-dlp: actualización y autorreparación

- **Actualizar yt-dlp = bump del pin + rebuild/redeploy; nunca auto-actualización en caliente** (§4.1): el pin exacto y el build reproducible mandan; el selfcheck completo corre en el primer arranque tras cada redeploy y corta una actualización rota antes del primer uso.
- **Detección de regresión post-actualización** (F-14): `last_known_good_ytdlp_version` persistido en el arranque y en cada selfcheck OK; si el selfcheck falla con versión distinta de la última buena → `daemon.selfcheck_broken_after_update` (la causa probable es la versión, no el hardware).
- **Notificación de nueva versión con dedupe por versión** (`last_notified_ytdlp_version`): el nightly publica a diario y sin dedupe la alerta sería diaria; el check usa la API de GitHub con ETag (un 304 no consume cuota).
- **`YTDLP_EXTRACTOR_ARGS` como válvula de escape** (§12): passthrough a extractor-args de yt-dlp para responder a degradaciones futuras del feed sin tocar código ni redesplegar lógica.

### 20.5 Backfill y cuentas

- **El daemon recoge backfills `queued` en el MVP** (§10): ningún encolado se queda sin ejecutor; el job comprueba el slot antes de crear la tarea y propaga el canal de eventos (T75). El slot sigue siendo por proceso (limitación documentada), pero el pacing sí es cross-proceso.
- **Throughput comunicado al usuario** (~40-150s/vídeo con los defaults; 1.000 vídeos ≈ 11-42h): el comando lo muestra antes de empezar; el backfill es reanudable por diseño (cursor + archive).
- **El cursor solo avanza en estado terminal** (§10) y el `break` del feed usa `scope_cursor` (snapshot) separado del cursor móvil (L-F1).
- **`backfill_total` se persiste al iniciar cada pasada** (F-09): el `done` es acumulativo (los ya archivados se saltan sin contar), así `done/total` converge; `skipped` cuenta como procesado.
- **Cancelación cooperativa real** (T21 + L-F5/L-F6): relectura periódica del estado + UPDATE condicional `WHERE backfill_status='backfilling'` + retorno temprano sin transición ni evento de completado.
- **Backfill y retry-failed descargan CON cookies** (F-01): adquieren `get_working_cookie()` al inicio y abortan con `backfill.no_cookies` si no hay ninguna.
- **`--then-monitor` con transición transaccional consumible** (§10): la `UPDATE` idempotente va en la misma transacción que el completado (T59), la bandera se consume, se emite `backfill.monitor_activated`, y hay reconciliación defensiva en el arranque. Solo desde `'completed'`, nunca desde `'failed'`/`'cancelled'`.
- **El modo `history` standalone se conserva deliberadamente**: el archivado puntual sin seguimiento es un caso de uso legítimo; retirarlo no simplificaría código; mantiene el default conservador de no iniciar actividad continua sin consentimiento. Validado contra Pinchflat y TubeArchivist.
- **`retry-failed` exige `@user`; la variante global exige `--all` con resumen y confirmación**: un reintento global es un lote de horas o días, no una operación casual.
- **`'paused'` es un valor reservado del esquema sin productor** (§2): los backfills interrumpidos vuelven a `queued`; solo se implementaría un productor si se retoma el backlog §18.

### 20.6 Notificaciones y Telegram

- **Catálogo con productores reales y test de paridad** (T34/F-08): cada evento tiene un emisor concreto; el test de paridad excluye `events.py` del scan para no ser vacuo. La auditoría encontró 14 de 27 plantillas sin emisor; se implementaron los 13 emisores que faltaban y se retiró `backfill.paused` del catálogo (sin mecanismo de pausa real).
- **Contexto accionable completo en notificaciones de descarga** (T61): fallos con URL + categoría + motivo + siguiente paso; éxitos con URL + ruta + duración/resolución/codecs verificados.
- **`download.completed` es el único opt-in** (`notify_on_download` por cuenta, propagado en TODAS las rutas — L-G3); `download.failed` y `download.retry_exhausted` son siempre accionables.
- **Spool guarda el evento original y re-renderiza al drenar** (F-06); solo con notificaciones habilitadas (T42).
- **Coalescing con `>=` umbral y bandera consumible** (L-I3); **clip() compartido con el sufijo DENTRO del límite** (F-07); **escape HTML en `event_message` Y en todos los handlers + degradación a texto plano** (F-05); **AIORateLimiter en bot y ExtBot** (T41).
- **Doble autorización: `TELEGRAM_CHAT_ID` + `from_user.id`** (§6.3): un chat de grupo daría control total a cualquier miembro; `TELEGRAM_USER_ID` configurable, default seguro = propietario del chat. Aplicado en comandos, callbacks Y documentos; intentos no autorizados → `bot.unauthorized_attempt` con `chat_id` y `from_user.id`.
- **Privacidad del import de cookies por Telegram**: el documento transita por servidores de Telegram y queda en el historial — README lo recomienda por CLI; tras importar por bot, `deleteMessage` best-effort + borrado del tempfile.
- **`getUpdates` manual PROHIBIDO contra un bot en polling** (T71): la Bot API solo permite una sesión; el bot muere en silencio sin que el daemon lo detecte. Verificación con `getMe`/`sendMessage`. La supervisión del polling queda en backlog (§18) con la advertencia verificada de PTB #3430.

### 20.7 Cookies y secretos

- **Validación en 3 estados con autocuración** (§7): `inconclusive` no toca `validation_state` ni `last_validated_at` (F-16); `get_working_cookie` solo rechaza ante `invalid` (L-E3).
- **Sonda de validación configurable con default `@rosary657`** (§7 — **decisión vigente, confirmada por el propietario del proyecto**): el default histórico fue `@tiktok` (embedding deshabilitado → falsos `inconclusive`, T74), luego `@dakpept` (funcionó tras iterar 5 entradas, pero sus primeras entradas son slideshows — frágil como default); el default consolidado es **`@rosary657`** (perfil verificado en producción con formatos extraíbles). La sonda itera las primeras 5 entradas (L-E4) y una sonda rota nunca invalida cookies (T57); `COOKIE_VALIDATION_URL` admite una lista separada por comas con fallback en orden (R12, §7) para no depender de una única cuenta de terceros.
- **Generación atómica de `fernet.key` con tolerancia a la ventana de archivo vacío en ambas ramas** (T67/L-E2).
- **Borrado del archivo fuente de cookies best-effort con `--keep-source`** (F-15): la salida informa del destino real (conservado/eliminado/NO eliminado).
- **`fernet.key` con backup fuera del volumen y del repo; su pérdida = purgar `cookies` y reimportar** (§0.1, §23.5.2); el selfcheck descifra una cookie real para detectar claves rotadas (T16).

### 20.8 CLI, configuración y estructura

- **CLI organizada en exactamente 7 grupos de sustantivo** (§3): sin comandos-verbo sueltos; pares antónimos simétricos; borrado siempre `remove`.
- **El daemon nunca hace fork** (§3): foreground siempre; background es responsabilidad del orquestador. (Se eliminó el flag `--foreground`, que era no-op.)
- **`MONITOR_INTERVAL_MINUTES`** (no `_MIN`): nombrado por unidad.
- **Salida CLI en ASCII puro** (L-A5) y exportaciones sin wrap ni markup de Rich (L-A6).
- **Toda variable de `.env.example` con efecto real o retirada** (T36): caso resuelto `WEBDAV_*` (F-17) — las lee el sidecar rclone, no la app; validarlas en la app podía bloquear el arranque por una feature no implementada.
- **Dockerfile y docker-compose.yml en la raíz del proyecto** (§11/§13): contexto de build `.`, `env_file: .env`; patrón oficial de uv (builder `python:3.13-slim` + binarios de uv + `UV_PYTHON_DOWNLOADS=0` + `--no-editable`; prohibido el builder distroless — F-03); `.dockerignore` re-incluye `README.md` (F-04).
- **Documentación = un único documento maestro** (§13): regla de archivos mínimos — el conocimiento acumulado vive en §19–§21 de este documento, no disperso en archivos históricos.
- **`system backup` y rotación round-robin de proxies están EN EL MVP** (F-21b): dejaron de ser backlog al confirmarse su implementación.

### 20.9 Calidad y proceso

- **Pre-commit con ruff + CI con ruff/pytest/cobertura/build+smoke Docker** (F-22): el smoke `docker run … --version` se añadió tras descubrir que un build exitoso no implica una imagen que arranca (T70).
- **Tests F.I.R.S.T. con entorno siempre mockeado** (T69): la implementación anterior tuvo un test que pasaba en Windows y fallaba en Debian 13 por consultar el disco real.
- **La auditoría externa (22 hallazgos F-01…F-22) se resolvió entera con tests de regresión** en la implementación anterior; sus correcciones de comportamiento están integradas en las secciones normativas de este documento y su detalle en §21.
- **Resolución de ambigüedades**: cualquier duda se resuelve por las 5 prioridades finales (§26).

---

## 21. Lecciones de la implementación anterior (registro de errores resueltos y incidentes)

> **Qué es esta sección**: el proyecto se reconstruye desde cero, pero la implementación anterior dejó un registro valiosísimo de errores reales de código (bugs, fallos de integración, comportamiento incorrecto) y de incidentes operativos. La nueva implementación **repetiría esos mismos bugs sin este registro**. Cada lección se organiza por área y muestra: **síntoma → causa raíz → regla a aplicar** (la regla ya está normativizada en la sección técnica correspondiente; esta sección es el "por qué" y el contexto).
>
> **Alcance**: solo errores de código y de interacción del código (los incidentes operativos puros están al final, §21.9). Un fallo de red o un bloqueo de TikTok no es un error de código (principio 11) — no entra.
>
> **Reglas de uso para la nueva implementación**: (1) cuando se resuelva un error nuevo, registrarlo en esta sección en el mismo turno (añadir la fila a la tabla del área correspondiente, o crear un área nueva si no encaja); (2) si el error revela una trampa nueva no documentada, añadirla antes a §19/§20 como enmienda y referenciarla aquí; (3) los errores definitivos de diseño se registran en §20, no aquí.

### 21.1 Área A — CLI, typer y salida de consola

| ID | Síntoma | Causa raíz | Regla / solución aplicada |
|---|---|---|---|
| L-A1 | `tikdown-rs --help` lanza `RuntimeError: Could not get a command for this Typer instance` | Una instancia `typer.Typer` sin comandos ni callback no puede invocarse como console script: no hay forma de construir el comando de entrada | `@app.callback()` con flag global `--version` e `invoke_without_command=True` (el callback se ejecuta sin subcomando; `no_args_is_help` sigue mostrando ayuda sin argumentos) |
| L-A2 | `uv run tikdown-rs` → `error: program not found` | El paquete no declaraba el entry point de la CLI | Declarar `[project.scripts] tikdown-rs = "cli.main:run"` en `pyproject.toml` |
| L-A5 | `UnicodeEncodeError: 'charmap' codec can't encode '\u2717'` al ejecutar comandos en consola Windows legacy (cp1252, salida con pipe) | Los glifos no-ASCII de la salida CLI (`✓`, `✗`, `—`, `⚠`, `sí`) no son representables en cp1252; rich escribe por el stream del proceso con su encoding cuando no hay terminal real | Marcadores ASCII puros en TODA la salida CLI (headers de tabla, valores sí/no, `OK`/`ERROR`, `-` para vacíos, `!` para alertas); tests que asertan headers ASCII y smoke con salida piped |
| L-A6 | `videos export` corrompía JSON/CSV largo (Rich envuelve a 80 cols en no-TTY) y un título con `[/]` reventaba `videos last` con `MarkupError`; una etiqueta desconocida (`[rojo]`) se consumía en silencio perdiendo datos | Rich interpreta markup y aplica wrap por defecto; las celdas de tabla y la exportación pasaban strings crudos | Export con `console.print(markup=False, soft_wrap=True)`; celdas dinámicas con `rich.text.Text()` (videos last, accounts list, cookies list) |
| L-A7 | Errores de negocio (`AccountError`, `BackfillAccountError`, `ConfigurationError`) reventaban la CLI con traceback y stdout vacío; `daemon run` mostraba traceback completo ante config inválida o selfcheck roto | Los comandos llamaban `run_async` sin mapear excepciones de servicio a salida limpia; solo se capturaba `RuntimeError` | `common.run_or_exit()`: convierte los errores de negocio en `ERROR <mensaje>` + exit 1, sin tracebacks; capturar `(RuntimeError, ConfigurationError)` en `daemon run` |
| L-A8 | `@username comprobada` con el literal en vez de la variable; comentarios de Windows y de `error_category` falsos/incompletos; `system backup` sin mapear `sqlite3.OperationalError`; `_guard` con `effective_chat` None; monitor importando el privado `_seconds_since` | Correcciones cosméticas y de doc drift acumuladas por la auditoría (F-21) | Variable real, comentarios exactos, mapeo de excepción, guard defensivo, `seconds_since` público — la calidad del mensaje CLI es UX, no cosmética |

### 21.2 Área B — asyncio, tareas supervisadas y ciclo de vida del daemon

| ID | Síntoma | Causa raíz | Regla / solución aplicada |
|---|---|---|---|
| L-B1 | El daemon NO se apagaba con `daemon stop`: el proceso seguía vivo con heartbeat stale (zombi) y sin logs de shutdown, pese a que el comando "funcionaba" sin error visible | El ciclo de vida usaba un `asyncio.run()` por fase (`start()`, `run()`, `shutdown()`): cada llamada crea UN NUEVO event loop. El scheduler y todos los jobs quedaban atados al loop de `start()`; al cerrarse ese loop, el heartbeat dejaba de ejecutarse y el watcher de `stop_requested` nunca disparaba el apagado | **TODO el ciclo (start + run + shutdown) corre dentro de UN único `asyncio.run(_lifecycle())`** — regla normativa en §5.1; test de regresión: el watcher detecta `stop_requested` y el apagado se completa |
| L-B2 | `RuntimeError: asyncio.run() cannot be called from a running event loop` en tests | Un helper de test usaba `asyncio.run()` para envolver una consulta, pero los tests ya corren dentro del event loop de pytest-asyncio (modo auto) | Helpers de test `async` con `await` en los tests; nunca `asyncio.run()` dentro de código async |
| L-B3 | El arranque del daemon fallaba con `RuntimeError: cannot be called from a running event loop` + warning `run_async_migrations was never awaited` | `apply_migrations()` delega en alembic, cuyo `env.py` hace `asyncio.run()` internamente; llamado desde dentro del loop del daemon revienta | Ejecutar en un hilo: `await asyncio.to_thread(apply_migrations, data_dir)` |
| L-B4 | `TypeError: Logger._log() got an unexpected keyword argument 'name'` en el callback de auditoría: toda tarea supervisada que fallaba crasheaba el callback | `log.error("...", name=..., exc_info=exc)` — stdlib logging no acepta `name` como kwarg (sí structlog) | Interpolar el nombre en el mensaje: `log.error("tarea supervisada falló: %s", task.get_name(), exc_info=exc)` |
| L-B5 | `daemon.stopped` creaba una tarea de notificación DESPUÉS del drenaje (con Noop) → `active_count` incorrecto en el apagado; y el evento casi nunca se entregaba por emitirse tras disponer recursos | `_on_event` creaba tarea supervisada incluso con el servicio Noop; el envío se programaba cuando nadie lo esperaría y el spool ya no tenía engine | Con `NoopNotificationService` no se crea tarea (solo log); `daemon.stopped` se emite ANTES del drenaje (F-19, §5.2) |

### 21.3 Área C — SQLite / SQLAlchemy

| ID | Síntoma | Causa raíz | Regla / solución aplicada |
|---|---|---|---|
| L-C1 | `sqlalchemy.exc.MissingGreenlet` al leer `acc.videos` | La relación `MonitoredAccount.videos` es lazy por defecto; en SQLAlchemy async el lazy-load implícito no puede ejecutarse fuera de una sesión/corutina | Cargar explícitamente con `select(MonitoredAccount).options(selectinload(...))` |
| L-C2 | `tikdown-rs videos last` crasheaba en producción: `DetachedInstanceError: lazy load operation of attribute 'account' cannot proceed` | Las filas salen de la sesión (detached) y el CLI accede a `video.account` (relación lazy) fuera de ella | `selectinload(Video.account)` en las consultas que reutilizan CLI y bot (p. ej. `query_last_videos`) — §13 |
| L-C3 | `backfill retry-failed --all` lanzaba `AttributeError: 'async_sessionmaker' object has no attribute 'execute'` | Se ejecutaba sobre el sessionmaker en vez de abrir una sesión | `async with session_factory() as session: await session.execute(...)` |
| L-C4 | Mutaciones a la cuenta que no persistían (cursor/throttle): el objeto quedaba detached | `get_account_by_username(...)` dentro de un `async with session` abre OTRA sesión; el objeto devuelto no pertenece a la sesión exterior y el commit exterior no persiste nada | Consultar dentro de la sesión activa con `session.execute(select(...))` |
| L-C5 | `sqlite3.OperationalError: database is locked` en el `PRAGMA journal_mode=WAL` de conexiones concurrentes: dos procesos arrancando a la vez sobre la misma base podían fallar en el connect | `journal_mode=WAL` toma un lock exclusivo y se ejecutaba ANTES de `busy_timeout` (cuyo default es 0 → fallo inmediato en vez de esperar) | `busy_timeout` PRIMERO en los PRAGMAs, luego `journal_mode`; el mismo orden en los helpers de los tests multiproceso — §16 |
| L-C6 | `pacing.reserve_contention_fail_open` en TODA reserva real con un único proceso: el cooldown cross-proceso estaba roto en producción (cada proceso sorteaba localmente sin coordinación — exactamente el riesgo T22). La fila `download_pacing_state` NO EXISTÍA ni tras `reserve()` | El `INSERT ... ON CONFLICT DO NOTHING` del singleton se ejecutaba en una sesión SIN COMMIT: al salir del `async with` la sesión hacía rollback, la fila nunca se creaba, el UPDATE condicional afectaba 0 filas y el CAS fallaba 20 veces → fail-open permanente. Los tests no lo detectaban: el fail-open devuelve un retardo > 0 y las aserciones solo comprobaban positividad | `await session.commit()` tras el INSERT del singleton — la fila persiste y el CAS converge; test que verifica la persistencia de la fila (§2, §4.5) |
| L-C7 | Con cooldowns pequeños (tests MIN=MAX=0.05) dos reservas consecutivas podían producir `next_allowed_at` IGUALES: `isoformat(timespec="seconds")` redondea al segundo y el CAS (`WHERE next_allowed_at = prev`) perdía la distinción | Precisión de segundos en el timestamp de pacing | `timespec="milliseconds"` (sigue siendo lexicográficamente comparable con el resto de timestamps ISO del proyecto) — §2 |
| L-C8 | El archive contenía líneas DUPLICADAS por vídeo: yt-dlp escribe `tiktok <id>` en el `--download-archive` y el `add()` añadía el id pelado (`<id>`); `contains()`/`discard()` no reconocían el formato de yt-dlp (el dedupe app-level dependía del espejo SQLite; sin espejo, re-descargaría) | El parser asumía un único formato (id pelado) | `_id_of_line()`: el ID es el ÚLTIMO token de la línea (formato propio y `extractor id`); `add()` no duplica, `discard()` elimina ambos formatos — §4.5 |
| L-C9 | `IndexError: list index out of range` al crear un engine sobre URL de memoria (`sqlite+aiosqlite://`, sin `///`) | `_ensure_parent_dir` asumía que toda URL no-memoria contenía `///`; el check era `:memory:` literal, no estructural | Comprobar `"///" not in url` en lugar del literal `:memory:` — §16 |

### 21.4 Área D — Motor de descarga y yt-dlp

| ID | Síntoma | Causa raíz | Regla / solución aplicada |
|---|---|---|---|
| L-D1 | `AssertionError` en TODA llamada real del motor: `yt_dlp.YoutubeDL(params)` reventaba en `_impersonate_target_available` → `is_supported_target` (`assert isinstance(target, ImpersonateTarget)`). El selfcheck pasa (solo cuenta targets); la primera descarga real fallaba | `_load_targets` construía targets como STRINGS (`"chrome:133"`); el CLI de yt-dlp parsea la cadena a objeto, pero el PARAM `params["impersonate"]` exige el objeto `ImpersonateTarget` | Conservar los OBJETOS `ImpersonateTarget` devueltos por `_get_available_impersonate_targets` (rotación RR sobre objetos) — §4.1 |
| L-D2 | `engine.download()` colgaba indefinidamente: `await network_available.wait()` nunca retornaba | El `asyncio.Event()` por defecto del motor se crea SIN setear (estado = offline). Sin `NetworkMonitor` inyectado, nadie lo activa nunca | El evento por defecto se crea y se setea (`default_event.set()`): sin monitor, la red se asume disponible; el monitor inyectado lo gestiona — §9 |
| L-D3 | Los tests del motor colgaban 30-120s por descarga | El cooldown global por defecto (30-120s) está activo en `Settings`; los tests de descarga dormían la reserva completa | `make_settings()` desactiva el cooldown por defecto (0,0) en tests; los tests de cooldown pasan valores explícitos — §14 |
| L-D4 | `AttributeError: 'YtDlpEngine' object has no attribute '_proxies'` en toda la suite del motor | Un edit con dos bloques falló atómicamente (uno no casó): el `__init__` perdió `self._proxies` pero el `_build_params` que lo referencia sí se aplicó | Re-añadir la inicialización en `__init__`; regla de proceso: los edits multi-bloque deben verificarse con un import tras aplicarlos |

### 21.5 Área E — Cookies y criptografía

| ID | Síntoma | Causa raíz | Regla / solución aplicada |
|---|---|---|---|
| L-E1 | `cookies test 1` → `inconclusive` — `'<tmp>' does not look like a Netscape format cookies file`; TODA la validación y el uso de cookies (backfill/monitor) roto en producción | `MozillaCookieJar._really_load` de CPython (el parser que usa `YoutubeDLCookieJar` de yt-dlp) exige que la PRIMERA línea case con `# Netscape HTTP Cookie File`. El blob cifrado solo guarda las líneas de cookies y el tempfile regenerado no tenía header. Los tests no lo detectaban: mockean `validate_cookie` y el parser propio tolera archivos sin header | Todo archivo Netscape reconstruido lleva el magic header (constante `NETSCAPE_HEADER`, sin duplicar) + `newline="\n"` explícito; los tests cargan el tempfile regenerado con el `YoutubeDLCookieJar` REAL — §7, T73 |
| L-E2 | `ValueError: Clave Fernet inválida` intermitente en Linux (~50% de ejecuciones del test de generación concurrente; en Windows casi nunca) — el primer arranque concurrente daemon + CLI en Linux podía abortar | Dos rutas compiten por crear `fernet.key`. El ganador crea el archivo con `open('xb')` (O_EXCL, **vacío**) y escribe DESPUÉS; el perdedor que entra por la rama directa de carga puede leer en la ventana entre creación y escritura: `b''` → ValueError. El fix original cubría solo la rama del perdedor del O_EXCL, no la directa | **Ambas ramas de carga** tratan el archivo vacío como ventana de creación: releer con reintentos (50×10ms); solo la corrupción no vacía o el vacío persistente se propagan — §5.1, T67 |
| L-E3 | En producción (cookie VÁLIDA con formatos de vídeo confirmados): `cookies test 1` → `inconclusive`, y como consecuencia `get_working_cookie` REchazaba la cookie (revalidación activa → inconclusive → `working_cookie_rejected`) → backfill/monitor abortarían con `no_cookies` teniendo una cookie perfecta | `get_working_cookie` trataba `inconclusive` como fallo (`state != "valid"` → rechazo) en lugar de solo `invalid` — un inconclusive no confirma ni descarta nada (T57/F-16) | `get_working_cookie` solo rechaza con `invalid`; `inconclusive` conserva la cookie con log informativo — §7 |
| L-E4 | Con la sonda por defecto anterior, `cookies test 1` → `inconclusive — no video formats found` con una cookie VÁLIDA: la sonda solo inspeccionaba la PRIMERA entrada del feed, y si es un slideshow solo-audio (frecuente incluso en perfiles buenos — issue upstream #12610), una cookie válida da `inconclusive` permanente | La sonda no contemplaba que la primera entrada del feed pueda ser un post de fotos sin formatos de vídeo | **La sonda itera las primeras `PROBE_MAX_ENTRIES=5` entradas buscando formatos de vídeo**; solo si NINGUNA los tiene devuelve `inconclusive`; las entradas sin `webpage_url` se saltan — §7, T74 |

### 21.6 Área F — Backfill y monitor

| ID | Síntoma | Causa raíz | Regla / solución aplicada |
|---|---|---|---|
| L-F1 | El backfill paraba tras el primer vídeo: el `break` del feed usaba el cursor MÓVIL (fecha del último procesado) en lugar del cursor de ALCANCE (progreso previo de la cuenta) | Tras procesar el vídeo más nuevo, cursor=su fecha; el siguiente (más viejo) era `< cursor` → break inmediato. El bug quedó oculto mientras el cursor local no se actualizaba (stale) | Separar `scope_cursor` (snapshot para el break) de `cursor` (móvil, para fallback y persistencia) — §10 |
| L-F2 | Una entrada del feed sin `createTime` recibía el cursor INICIAL de la cuenta en vez del cursor anterior | La variable local `cursor` no se actualizaba tras cada vídeo; el fallback `entry.upload_date or cursor` usaba el valor stale | `cursor = upload_date` tras persistir cada vídeo — §4.9 |
| L-F3 | Progreso siempre `N/0`: `backfill_total` nunca se escribía (solo lecturas en status/stats/CLI) | Ningún `UPDATE`/`INSERT` persistía el total; el CLI conocía `len(entries)` pero no lo guardaba | Persistir `backfill_total = len(entries)` al iniciar cada pasada (el `done` es acumulativo: los ya archivados se saltan sin contar, así `done/total` converge) — §10 (F-09) |
| L-F4 | Backfills wedged en `backfilling` para siempre tras crash/shutdown/fallo del feed (bloqueando `BackfillBusy` en reintentos posteriores) + tormenta de errores cada 30s en la recogida | (1) `list_videos` fuera del try catástrofe; (2) `CancelledError` (BaseException) no cubierto por `except Exception` en apagados; (3) sin reconciliación de arranque; (4) `collect_queued_backfills` creaba la tarea aunque el slot estuviera ocupado | Listado dentro del try; `except asyncio.CancelledError` → estado `queued` (auto-reanudable); `reconcile_stale_backfills()` en el arranque del daemon; `backfill_slot_busy()` antes de crear tareas — §10 (F-10) |
| L-F5 | La persistencia del cursor (UPDATE `backfill_status='backfilling'` + cursor + done) tras CADA vídeo pisaba un `backfill cancel` concurrente: el estado `cancelled` se sobrescribía y el backfill seguía hasta completar | `_set_status` incondicional después de `handle_download_result`; read-then-write en dos transacciones | `_persist_progress()`: UPDATE condicional con `WHERE backfill_status='backfilling'` (rowcount 0 → cancelación detectada sin sobrescribir nada) — §10, T21 |
| L-F6 | `run_backfill` pisaba el outcome `cancelled` de `_process_entries` con `completed` (y ejecutaba la transición y `backfill.completed`) | El flujo continuaba tras la cancelación cooperativa sin comprobar el outcome | `return outcome` temprano si `outcome.status == 'cancelled'` (sin transición ni evento de completado) — §10, T21 |
| L-F7 | `backfill cancel` → `IntegrityError: CHECK constraint failed: ck_accounts_backfill_status` | La tabla del modelo omitía `'cancelled'` en el enum de `backfill_status` (T21/§3/§10 lo requieren) | `'cancelled'` añadido al CHECK (modelo + migración inicial) — §2 |
| L-F9 | El backfill descargaba SIN cookies: el parámetro `cookiefile` se encadenaba hasta `_run_locked` pero la llamada real a `handle_download_result` no lo incluía, y ningún llamador adquiría `get_working_cookie`; el listado del feed tampoco usaba cookie en ninguna ruta | Parámetro muerto por un edit incompleto + ausencia de adquisición en las rutas (solo el monitor lo hacía); invisible en tests porque los fakes usan `**kwargs` y nunca lo echan de menos | Adquisición de working cookie en `run_backfill`/`retry_failed_videos` (con `cookiefile` inyectable con prioridad); aborto con el evento `backfill.no_cookies` si no hay cookies; `cookiefile` propagado al embudo y a `list_videos` — §10 (F-01) |
| L-G1 | El throttle de 30s saltaba cuentas NUNCA comprobadas: `last_check_at=None` → `seconds_since=0 < 30` → el ciclo del monitor y `accounts check` se saltaban cuentas recién añadidas sin comprobarlas jamás | El throttle no distinguía "recién comprobada" de "nunca comprobada" (`None` se trataba como 0 segundos) | Throttle solo cuando `last_check_at` existe; una cuenta sin marca se comprueba siempre — §4.9 |
| L-G2 | Vídeo nuevo real descargado por el monitor SIN notificación `download.completed` pese a `notify_on_download=True`: en el log solo `monitor.new_videos_found` y ningún evento de descarga; spool vacío y sin errores | `run_monitor_cycle` envolvía el canal SÍNCRONO del daemon en un closure `async def _emit` y lo pasaba a `handle_download_result(on_event=emit)`; el contrato del canal es síncrono (el handle lo invoca SIN `await`) → se creaba una corrutina que nunca se ejecutaba (evento perdido en silencio). Los tests no lo detectaban: usan lambdas síncronas | `handle_download_result` recibe y propaga el canal SÍNCRONO `on_event` directamente; el wrapper async solo se usa para los eventos del propio ciclo (con `await`) — §4.6 |
| L-G3 | Backfill encolado real (notify_on_download=ON): llegaron `backfill.queued`/`completed`/`monitor_activated` a Telegram pero NUNCA `download.completed` — el plan exige que lo emita el monitor Y por backfill | `handle_download_result` se llamaba sin `notify_on_download` (default False) en las dos rutas del backfill; solo el monitor lo propagaba | `notify_on_download=bool(account.notify_on_download)` en `_process_entries` y `retry_failed_videos` — §4.6, §8 |

### 21.7 Área H — Bot de Telegram

| ID | Síntoma | Causa raíz | Regla / solución aplicada |
|---|---|---|---|
| L-H2 | `RuntimeError: To use AIORateLimiter, PTB must be installed via pip install "python-telegram-bot[rate-limiter]"` al construir el bot | `AIORateLimiter` vive en el extra `[rate-limiter]` (aiolimiter) de PTB, no en el paquete base | Dependencia `python-telegram-bot[rate-limiter]>=22,<23` — §1 |
| L-H5 | El tempfile del upload de cookies NO se podía eliminar en Windows: `PermissionError: being used by another process` — el borrado best-effort del import fallaba y el tempfile quedaba huérfano | `tempfile.mkstemp` devuelve un fd que el bot nunca cerraba (`os.close`); en Windows un handle abierto impide el unlink (en Linux no) | `os.close(fd)` inmediatamente tras `mkstemp` (la ruta se asigna justo tras la creación, T31) — §6.3 |
| L-H6 | Tanda E2E de comandos reales: `/check @dakpept` respondió `@@dakpept: 17 vídeos en el feed` (doble `@`); los otros 21 comandos respondieron correctamente | `_cmd_check` interpolaba `f"@{args[0]}"` sin `lstrip('@')` — el resto de handlers sí lo hacen | `args[0].lstrip('@')` en el mensaje de respuesta — §6.4 |
| L-H7 | Mensajes con `@@usuario` (doble @) en todas las plantillas con `{username}` | El template ya incluye el literal `@` y el render añadía otro `@` al escapar el username | El render de `username` no añade `@` (el template lo lleva) — §8 |

### 21.8 Área I — Notificaciones y eventos

| ID | Síntoma | Causa raíz | Regla / solución aplicada |
|---|---|---|---|
| L-I1 | Un fallo de red con excepción NO-TelegramError (p. ej. httpx) reventaba `send_event` y bloqueaba el flujo que lo originaba | `_send` solo capturaba `TelegramError`; los fallos de red pueden surfacear como cualquier excepción | Captura amplia `except Exception` con spool (T42) cuando está habilitado — §8 |
| L-I3 | El resumen de coalescing solo se generaba en el instante exacto del umbral: si el llamador consultaba `take_summary()` después (con el contador ya por encima), devolvía None y la ráfaga se perdía en silencio | Condición `count == THRESHOLD` en lugar de `>= THRESHOLD` con bandera consumible | `>= THRESHOLD` + `summarized` (una sola emisión por ventana) — §8 |
| L-I5 | Backfill completado (17/17) y transición `--then-monitor` aplicada SIN eventos ni notificaciones: en logs solo `backfill.queued`; en Telegram solo los mensajes que sí se enviaron — ningún `backfill.completed`/`monitor_activated`/`download.*` pese a sendMessage 200 OK | `collect_queued_backfills` recibe `on_event` (lo usa para emitir `backfill.queued`) pero NO lo propagaba a `run_backfill(...)` → la corrutina del backfill corría con el canal de eventos en `None`. La ruta CLI foreground sí propagaba. Invisible en tests: el stub del registry cierra la corrutina sin ejecutarla | `on_event=on_event` en la llamada a `run_backfill`; test con wrapper SÍNCRONO que captura los kwargs en la creación — §5.1, T75 |
| L-I6 | Test de paridad plantilla↔productor vacuo: 14 de 27 plantillas del catálogo nunca se emitían | El scan de literales incluía `core/notifications/events.py` (que contiene el catálogo entero) → la segunda aserción no podía fallar | Test excluye `events.py`; se implementan todos los emisores faltantes — §8, T34/F-08 |

### 21.9 Área J — Migraciones, logging y despliegue (incluidos incidentes operativos)

| ID | Síntoma | Causa raíz | Regla / solución aplicada |
|---|---|---|---|
| L-J1 | Cada comando CLI imprime `INFO [alembic.runtime.migration] …` en stdout (ruido en cada invocación) | El logger `alembic` estaba en `INFO` con handler de consola; las migraciones se ejecutan en cada invocación (§5.5) | Nivel del logger `alembic` a `WARNING` en `alembic.ini` |
| L-J3 | `docker logs` del contenedor con **0 bytes** mientras el daemon corría (heartbeat fresco, health healthy): ningún log de arranque, eventos ni watcher | El arranque llama `_setup_logging()` (JSON a stdout, INFO) ANTES de las migraciones; `alembic/env.py` hace `fileConfig()` que RECONFIGURA el root logger con `[logger_root] level = WARNING` (handler stderr genérico). `disable_existing_loggers=False` evitaba deshabilitar loggers existentes, pero el ROOT quedaba pisado | Reaplicar `_setup_logging(settings.log_level)` inmediatamente después de `apply_migrations` — `basicConfig(force=True)` elimina el handler de Alembic y restaura el formatter JSON y el nivel — §5.1, T72 |
| L-J4 | La imagen Docker oficial no arrancaba: contenedor en `Restarting(1)` con `CommandError: No 'script_location' key found`; el traceback mostraba el módulo cargado desde site-packages | `uv sync --no-editable` instala un wheel: `core/migrations.py` vive en site-packages y `Path(__file__).resolve().parents[1]` apuntaba ahí, donde NO hay `alembic.ini` (el wheel solo empaqueta paquetes de código). El smoke de CI (`--version`) no ejecuta migraciones → el bug solo aparecía en runtime | `_find_alembic_ini()` con candidatos: (1) junto al módulo (dev editable), (2) cwd (el Dockerfile hace `COPY . .` + `WORKDIR /app`); error explícito y accionable si ninguno existe — §5.5, T70 |
| L-K1 | El build de la imagen fallaba con `OSError: Readme file does not exist: README.md` en la segunda pasada de `uv sync`; y aunque pasara, el runtime no habría arrancado (venv con intérprete inexistente) | (1) `.dockerignore` excluía `*.md` y hatchling exige el README declarado en `pyproject.toml`; (2) el builder era la imagen distroless `ghcr.io/astral-sh/uv:latest` (solo binarios, sin Python): `uv sync` descargaba un CPython gestionado y el `.venv` copiado al runtime lo referenciaba (intérprete colgante) | Dockerfile con patrón oficial (builder `python:3.13-slim` + binarios de uv + `UV_PYTHON_DOWNLOADS=0` + `--no-editable`); `!README.md` en `.dockerignore`; smoke `docker run --rm … tikdown-rs --version` añadido a CI — §11 (F-03/F-04) |
| L-K3 | El build de la imagen fallaba: `Syntax error - can't find = in "#". Must be of the form: name=value` en la instrucción `ENV` multilínea | El comentario `# usa el CPython...` estaba DENTRO de la instrucción ENV continuada — el parser de Docker no admite comentarios inline tras la continuación `\` | Comentarios en líneas propias antes de la instrucción ENV — §11 |
| L-K4 | Todos los workflows de CI fallaban en 0s con `conclusion=startup_failure`, incluso uno mínimo (`on: [push]` + un echo) | Diagnóstico: (1) el YAML era válido y sin CRLF, (2) los SHAs de las acciones eran accesibles, (3) el fallo se reproducía en un repo nuevo público y privado. Causa raíz: **billing de la cuenta** — "recent account payments have failed" | `gh workflow disable` (reversible con `gh workflow enable` al resolver el billing); no era un problema de código — §1.3 |
| L-J5 | Diagnóstico manual con `getUpdates` contra el bot en polling → `telegram.error.Conflict` (409): el bot murió en silencio, el daemon siguió healthy (T71) | Error de operación + debilidad de resiliencia documentada (la supervisión del polling queda en backlog §18) | Nunca verificar el bot con `getUpdates` manual: usar `getMe`/`sendMessage`. Recuperación: reiniciar el contenedor — §6.1 |
| L-J6 | `cookies test 1` con cookie REAL → `inconclusive` — `No video formats found` con la sonda `@tiktok` (embedding deshabilitado) (T74) | Observación operativa — el sistema se comportó correctamente (`inconclusive` sin invalidar, T57) | La sonda debe verificarse con una extracción real antes de desplegarla (`yt-dlp -s <perfil>` devuelve formatos); default consolidado: `@rosary657` — §7 |

### 21.10 Área L — Tests y proceso (lecciones transversales)

| ID | Síntoma | Causa raíz | Regla / solución aplicada |
|---|---|---|---|
| L-L1 | Tests placeholder que escribían en rutas absolutas fijas (`C:/tmp/…`) fuera del suite | Patrón de test con rutas hardcodeadas del desarrollador | Los tests nunca escriben fuera de su `tmp_path` — §14 |
| L-L2 | (1) Aserción dependiente de la hora del día: expiración epoch 100 (1970) daba `expires_in == 0`; (2) `StubEngine` del monitor sin `validate_cookie` → AttributeError; (3) mock de `Path.unlink` sin aceptar kwargs | Datos de test dependientes del reloj y stubs incompletos respecto a la interfaz real usada | (1) timestamp futuro calculado con `datetime.now+timedelta`; (2) stub con `validate_cookie`; (3) mock con `*args/**kwargs` — §14 |
| L-L3 | TypeError al ampliar `handle_download_result` con kwargs: los fakes no los aceptaban. El TypeError se enmascaraba como `BackfillAccountError` (el catch catástrofe del backfill lo envuelve) | Fakes con firma incompleta; el wrap de excepciones del backfill dificulta el diagnóstico | Ampliar los fakes con `**kwargs` |
| L-L4 | `AttributeError: Attribute 'send_message' of class 'ExtBot' can't be set!` | `ExtBot` usa slots: no se puede `setattr` su método; el test parcheaba el método en vez del bot | Sustituir `service._bot` por un fake completo con `send_message` |
| L-L5 | `zip(ordenadas, ordenadas[1:], strict=True)` → `ValueError: zip() argument 2 is shorter than argument 1` | Compara listas de distinta longitud (n vs n-1) y `strict=True` lo convierte en error | Usar `itertools.pairwise(ordenadas)` (Python ≥3.10) |
| L-L6 | (1) Los hijos pasaban `DATA` como `str` a `database_url` (espera `Path`); (2) los hijos usaban los singletons sin migrar (no such table); (3) `apply_migrations` (alembic, asyncio.run) llamado dentro de un test async | Scripts de subprocesos con tipos sueltos y sin el ciclo de vida real (migrar antes de usar la DB) | `Path(DATA)` en los hijos + `apply_migrations` al inicio + `asyncio.to_thread` en el test async — §14 |
| L-L7 | `test_disk_check_avisa_bajo_umbral` fallaba en Debian 13 (`free_percent=99.89, warned=False`); en el baseline local (Windows) pasaba | Los tests de disco consultaban `shutil.disk_usage()` REAL del entorno con umbrales asumidos (99%): en la instancia `tmp_path` cae en `/tmp` (tmpfs, 99.9% libre) → nunca avisaba. Test no determinista (F.I.R.S.T. violado) | Mock de `shutil.disk_usage` con % libre controlado (`_disk(free_pct)`) en los tests de disco; umbrales explícitos e independientes del entorno — §14 (T69) |
| L-L8 | `AssertionError` por comparar `str(Path)` (con `\` en Windows) contra una cadena con `/` fija | Aserción dependiente de plataforma | Resolver el outtmpl con los placeholders sustituidos y comparar como `Path` — §14 (T8) |

### 21.11 Reglas de proceso derivadas (para toda la vida del proyecto)

1. **Registrar cada error resuelto en esta sección en el mismo turno** — nunca al final; si el error revela una trampa nueva, añadirla primero a §19/§20.
2. **Un error transitorio o de red nunca se registra como defecto de código**, ni invalida cookies ni consume reintentos (principio 11, T64).
3. **Los fallos de limpieza posteriores a un éxito confirmado son best-effort**: se registran como advertencia y el resultado global sigue siendo éxito (T14).
4. **Los tests son F.I.R.S.T.**: deterministas, sin entorno real, sin reloj físico, sin rutas absolutas.
5. **Verificar los edits multi-bloque con un import tras aplicarlos** (L-D4): un edit que no casa deja el código en estado inconsistente silencioso.
6. **Los fakes y stubs replican la firma real de la interfaz** (`**kwargs`, todos los métodos) — una firma incompleta enmascara bugs de parámetros muertos (L-F9, L-L3).
7. **Un cambio de firma actualiza todos los llamadores** (rename `cancel_pending(timeout)` → `timeout_seconds` rompió tests y llamadores).
8. **Duda de diseño/alcance** → el plan manda; si el plan no la resuelve, decidir por las 5 prioridades de §26.

---

## 22. Política de seguridad

> **Rol de esta sección**: política de seguridad **normativa** del proyecto. Es transversal: sus requisitos se aplican a todo el desarrollo, despliegue y operación, y **prevalecen sobre cualquier otra sección de este documento en caso de conflicto** (si un texto del plan técnico contradice un requisito aquí, lo resuelve esta política; las resoluciones quedan registradas en §20).

### 22.1 Modelo de amenazas y límites de confianza

**TikDown-rs no expone ninguna superficie de red propia.** No hay servidor HTTP, no hay puertos de control, no hay autenticación basada en red, no hay frontend. El acceso al sistema se produce exclusivamente por:

1. **Acceso físico/shell al host o `docker exec`** — es el plano de confianza raíz. Quien tiene shell tiene todo el sistema (incluida la clave Fernet y las cookies descifrables).
2. **Telegram** — cuando el bot está en modo `commands`/`both`: solo el `TELEGRAM_CHAT_ID` configurado, y dentro del chat, solo el `from_user.id` autorizado (por defecto el propietario del chat configurado), puede ejecutar comandos.
3. **Egreso de red** — el proceso necesita salida HTTPS a `tiktok.com`, a la Bot API de Telegram y a los endpoints del probe/configuración. **Nunca escucha en ningún puerto.**

**Activos a proteger** (por orden de sensibilidad):
- `fernet.key` / `FERNET_KEY` — descifra TODAS las cookies almacenadas. Pérdida = pérdida permanente de todas las cookies (no hay recuperación del ciphertext).
- Cookies de sesión de TikTok (`encrypted_blob`) — equivalen a acceso a la cuenta del usuario.
- `TELEGRAM_BOT_TOKEN` — control del bot (envío de mensajes al chat autorizado; si el modo es `commands`, control remoto del daemon).
- `TELEGRAM_CHAT_ID`, credenciales WebDAV, `.env` real de despliegue.
- Los vídeos archivados (pueden ser indirectamente sensibles).

**Amenazas explícitamente fuera del modelo** (aceptadas y documentadas): un atacante con shell en el host, un atacante con acceso físico al volumen de datos sin la clave Fernet (las cookies siguen cifradas; los vídeos no), el proveedor del host/VM, un malware que ejecute código en el contexto del daemon (misma confianza que el operador).

### 22.2 Propiedades de seguridad (diseño)

- Sin servidor HTTP, sin frontend, sin autenticación de red. Acceso = shell/`docker-exec` en el host + el `TELEGRAM_CHAT_ID` (y `from_user.id`) permitido cuando el bot está en modo `commands`.
- Cookies cifradas en reposo con Fernet (`cryptography`); la clave vive en `DATA_DIR/fernet.key` (permisos `0600`, verificados y corregidos también sobre una clave existente — T7) o en la variable `FERNET_KEY`.
- `yt-dlp` + `curl-cffi` es el **único** cliente contra dominios de TikTok (principio 2). `httpx` solo se usa contra la Bot API de Telegram y los endpoints del probe de red, nunca contra TikTok.
- Coordinación CLI↔daemon exclusivamente vía SQLite (WAL) en el mismo host — no hay sockets de control que puedan escucharse por error.

### 22.3 Gestión de secretos (obligatoria desde el primer commit)

1. **`.gitignore` desde el commit inicial**: `.env`, `*.db*`, `/app/data/`, `videos/`, `fernet.key`, `*.session`, `cookies*.txt|json`, venvs, `__pycache__/`.
2. **`.dockerignore` obligatorio y completo** (T15): sin él, `COPY . .` embebe `.env`, el volumen, `fernet.key`, bases de datos y cookies en capas de imagen recuperables. Excepción verificada: re-incluir `README.md` (hatchling lo exige, F-04).
3. **`.env.example` con solo valores de ejemplo o vacíos** — nunca un token, chat ID o clave real, ni siquiera "de prueba".
4. **Ningún secreto en código ni en tests**: los tests de cifrado generan la `FERNET_KEY` al vuelo en el fixture.
5. **`fernet.key`**: backup obligatorio fuera del volumen y del repositorio (gestor de secretos o almacenamiento cifrado separado). Procedimiento de recuperación: si se pierde la clave, la única salida válida es purgar la tabla `cookies` y reimportar cookies frescas (§23.5.2). `daemon selfcheck` descifra una cookie almacenada para detectar claves rotadas o incorrectas de forma temprana (T16).
6. **Historial git limpio antes del primer push público**: si un secreto llegó a commitearse, reescribir el historial (`git filter-repo`) — un secreto en el historial es recuperable aunque ya no esté en el HEAD.
7. **README público** debe documentar qué NO commitear y el disclaimer legal (archivar solo contenido propio o permitido; la responsabilidad sobre los ToS de TikTok y el copyright es del usuario).

### 22.4 Telegram: autenticación y abuso

- **Autorización de doble capa**: solo el `TELEGRAM_CHAT_ID` configurado, y además el `from_user.id` autorizado (por defecto el propietario del chat configurado; configurable vía `TELEGRAM_USER_ID`, §12). La verificación por chat solo NO es suficiente si el chat permitido es un grupo: cualquier miembro tendría control total del daemon.
- La verificación se aplica en **comandos de texto, callbacks de botones inline y documentos subidos**, no solo en el dispatcher de comandos.
- Cualquier intento no autorizado emite `bot.unauthorized_attempt` (auditoría por log con `chat_id` y `from_user.id`).
- Throttle de 1 comando cada 2s por chat (también callbacks) para mitigar abuso y rate limits.
- Botones inline con expiración **real** (timestamp validado, 60s), no solo visual.
- Límite de upload de cookies: 10 MB verificado por metadato remoto Y por tamaño real post-descarga (el metadato puede ser 0, estar ausente o manipulado).
- **Privacidad del import por Telegram**: subir una cookie de sesión la hace transitar por los servidores de Telegram y queda en el historial del chat. El README debe recomendarlo; el import por CLI es la vía preferente. Tras importar por bot: `deleteMessage` best-effort + borrado del tempfile.
- Verificación del bot SIEMPRE con `getMe`/`sendMessage` — **nunca con `getUpdates` manual** contra un bot en polling (T71: la Bot API solo permite una sesión de `getUpdates`; una segunda sesión mata el polling con `Conflict` 409 y el bot queda muerto en silencio).
- **Rotación de `TELEGRAM_BOT_TOKEN` *[Añadido en el análisis posterior]***: el plan documenta backup y recuperación de `fernet.key` (§0.1, §23.5.2) pero no qué hacer si el token del bot se filtra o se quiere rotar preventivamente. Procedimiento: revocar el token existente con `@BotFather` (`/revoke`), generar uno nuevo, actualizarlo en `.env` y reiniciar el daemon (`docker compose up -d` reaplica el `env_file`). No requiere cambios de esquema ni migración — el token no se persiste cifrado en base de datos, solo en `.env`/entorno del proceso.
- **Registro reforzado de intentos no autorizados repetidos *[Añadido en el análisis posterior]***: `bot.unauthorized_attempt` (§22.4) ya audita cada intento individual, pero el plan no distinguía un intento aislado (alguien escribió al bot equivocado) de un patrón de tanteo sostenido. Sin añadir estado nuevo en base de datos ni un mecanismo de baneo (que introduciría complejidad de gestión de listas y expiración fuera de alcance del MVP homelab): si se detectan **5 o más intentos no autorizados en 5 minutos** desde el mismo `from_user.id` (contador en memoria del proceso del bot, con la misma limitación de reinicio que el circuit breaker de cuentas, §4.4), se emite una única alerta agregada `bot.unauthorized_attempts_burst` con el conteo y el `from_user.id`, en vez de una notificación individual por intento — evita tanto el ruido de Telegram ante un tanteo automatizado como el silencio total.

### 22.5 Despliegue seguro

- **Docker**: imagen multi-stage mínima (runtime `python:3.13-slim` + `ffmpeg` + solo el `.venv`); `HEALTHCHECK` con `tikdown-rs daemon healthcheck` (exit 0 solo con heartbeat fresco, T50); un único volumen para `DATA_DIR`; `--start-period` ≥ duración del selfcheck de arranque. Build multi-arquitectura con selfcheck de impersonación verificado en el hardware real (§4.1).
- **Hardening de contenedor Docker**: usuario no root (`user: nobody`), `tmpfs` para temporales (`--tmpfs`), `no-new-privileges: true`, `--cap-drop=ALL`, `--security-opt` acotado de familias de direcciones, `UMask=0077`. **No usar `--security-opt seccomp=unmasked`** — incompatible con el intérprete de Python. Combinar con `--read-only`, `read_only: true` y políticas de `seccomp`/`apparmor` restrictivas. Detalles en §23.3.
- **Firewall**: no hay puertos de control; el daemon no escucha nada. Si se usa el bot, solo salida HTTPS. El sidecar WebDAV (opcional) exige: solo lectura, autenticación obligatoria, detrás de reverse proxy con HTTPS o VPN (Tailscale/WireGuard); **nunca expuesto a internet directamente**.
- **Actualizaciones**: rebuild reproducible (`uv.lock` + pins exactos, incluyendo el pin exacto del nightly de yt-dlp); el selfcheck de arranque corta una actualización rota antes del primer uso (`last_known_good_ytdlp_version` + `daemon.selfcheck_broken_after_update`).

### 22.6 Seguridad de las dependencias

- **Pins exactos para todo lo crítico**: `yt-dlp` (nightly con fecha exacta), `curl-cffi` (pin exacto, preferentemente el extra `pin-curl-cffi` de yt-dlp). Un pin abierto de `curl-cffi` rompe la impersonación silenciosamente (T6).
- **Sin prerelease global**: `[tool.uv] prerelease-package = { "yt-dlp" = "allow" }`, nunca `prerelease = "allow"` global — permite que cualquier dependencia (p. ej. `pydantic`) resuelva a una alpha en el lock (T2, riesgo de cadena de suministro; confirmado por la documentación oficial de uv).
- `cryptography` se mantiene actualizada por motivos de seguridad, no solo de features.
- Reverificar versiones contra PyPI/GitHub antes de fijar `pyproject.toml` (procedimiento en §1.2).
- **Escaneo periódico de CVEs conocidas *[Añadido en el análisis posterior]*** (T76, §1.3): un pin exacto protege de romper compatibilidad entre releases, no de una vulnerabilidad publicada *después* de fijar el pin. Job semanal en CI (`pip-audit` sobre `uv.lock` + `trivy image` sobre la imagen construida) — detalle y justificación completos en §1.3.

### 22.7 Reporte de vulnerabilidades

- Reporta problemas de seguridad **vía GitHub issues** en el repositorio (o, si el repositorio no es público aún, directamente al mantenedor).
- **No incluyas secretos reales, cookies ni tokens en ningún reporte** — ni en el texto, ni en adjuntos, ni en capturas. Si el hallazgo implica un secreto concreto, describe el vector sin reproducir el valor y usa un canal privado para los detalles.
- Para una vulnerabilidad que compromete el modelo de amenazas de §22.1, reportar antes de publicar detalles (divulgación coordinada): un issue con etiqueta privada/borrador o un correo al mantenedor, y publicar el detalle tras el fix.

### 22.8 Responsabilidad legal

La herramienta está diseñada para archivar **contenido propio o permitido**. La responsabilidad sobre los ToS de TikTok y el copyright del contenido descargado recae en el usuario (disclaimer en el README, estilo yt-dlp). El proyecto no facilita la evasión de controles de acceso de contenido ajeno.

---

## 23. Despliegue y operación (guía práctica)

Guía operativa paso a paso para poner TikDown-rs en producción en un homelab (Linux/Docker). Toda ruta de datos deriva de `DATA_DIR`; el daemon es el único proceso de larga duración y se coordina con el CLI a través de SQLite (WAL).

### 23.1 Requisitos previos

| Requisito | Detalle |
|---|---|
| SO | Linux (recomendado Debian 13/Ubuntu 24.04) o Docker. Windows solo para desarrollo local |
| Python | 3.13 (fijado por `.python-version` y `requires-python`) |
| `uv` | ≥0.12 (`curl -LsSf https://astral.sh/uv/install.sh \| sh`) |
| `ffmpeg` / `ffprobe` | **Dependencia dura** (T46): merge de formatos e integridad. El daemon **no arranca sin ellos** |
| Red | Salida a `tiktok.com`, `api.github.com` (check de yt-dlp) y los endpoints del probe (`NETWORK_PROBE_URL`); nunca exponer puertos |

> **Hardware**: cualquier x86_64 moderno. Para `linux/arm64` (Raspberry Pi, NAS), verificar ANTES de comprometerse que la combinación yt-dlp/curl-cffi tiene wheels y que el selfcheck de impersonación pasa (§4.1). Un mini-PC amd64 es la opción sin fricción.

### 23.2 Estructura de despliegue

```
DATA_DIR/
├── tikdown-rs.db          # base de negocio (WAL) — SQLite
├── tikdown-rs.db-wal/shm  # WAL (no tocar; se regeneran)
├── fernet.key             # clave de cifrado de cookies (0600) — HACER BACKUP
├── download_archive.txt   # deduplicación append-only (yt-dlp)
├── .migrate.lock          # lock de migraciones entre procesos
├── backups/               # snapshots `system backup` (VACUUM INTO)
└── videos/                # los vídeos, por cuenta: videos/<usuario>/<id>.mp4
```

No hay servidor HTTP ni puertos de control: el daemon y los comandos CLI comparten el mismo `DATA_DIR` (Docker: volumen único).

> **Almacenamiento de `DATA_DIR` (requisito, no recomendación)**: SQLite en modo WAL no funciona de forma fiable sobre sistemas de archivos de red (NFS/SMB) — los locks de fichero y el `mmap` que WAL requiere no están garantizados ahí, y la coordinación CLI↔daemon (§0) se degradaría de forma intermitente y silenciosa. `DATA_DIR` debe residir en disco local del host (o en un volumen Docker respaldado por disco local). Un NAS montado por red es válido como destino de backups (§23.3.6) o para servir los vídeos por WebDAV (§17), nunca como almacenamiento vivo de la base de datos.

### 23.3 Opción A — Docker (recomendada)

#### 23.3.1 Construcción

```bash
# En el directorio del proyecto:
cp .env.example .env        # y editar (sección 23.3.3)
docker compose up -d --build
```

El `Dockerfile` (raíz) es multi-stage con el patrón oficial de uv: el builder (`python:3.13-slim` + binarios de uv, `UV_PYTHON_DOWNLOADS=0`) resuelve las dependencias con `uv sync --frozen --no-editable` (reproducible por el `uv.lock`); el runtime es `python:3.13-slim` con `ffmpeg` instalado y solo el `.venv` — sin uv ni caché de build en la imagen final. **No usar la imagen distroless `ghcr.io/astral-sh/uv:latest` como builder** (deja un venv con intérprete colgante — L-K1). El `docker-compose.yml` (raíz) usa contexto `.` y `env_file: .env`.

Para multi-arquitectura (opcional):

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t tikdown-rs:latest .
```

**Obligatorio antes de operar en ARM64**: ejecutar `tikdown-rs daemon selfcheck` en el hardware real (T6: la impersonación TLS puede no estar disponible en aarch64 incluso con la librería instalada).

#### 23.3.2 Arranque y verificación

```bash
docker compose up -d
docker compose logs -f tikdown-rs          # JSON a stdout
docker compose exec tikdown-rs tikdown-rs daemon status
docker compose exec tikdown-rs tikdown-rs daemon selfcheck   # exit 0 = OK
docker compose exec tikdown-rs tikdown-rs system disk
```

El `HEALTHCHECK` del contenedor ejecuta `tikdown-rs daemon healthcheck`: **exit 0 solo si el heartbeat es fresco** (≤ 3 × `HEARTBEAT_INTERVAL_SECONDS`). Un daemon zombi o detenido se marca `unhealthy`. El `--start-period` debe cubrir el selfcheck de arranque.

#### 23.3.3 Configuración (`.env`)

Copiar `.env.example` a `.env` y rellenar las variables necesarias. **El `.env.example` contiene solo valores de ejemplo o vacíos** — nunca un token, chat ID o clave real, ni siquiera "de prueba": un valor con forma realista invita a que alguien lo pegue sin cambiarlo. Las explicaciones de cada variable viven documentadas en el README; aquí solo se listan las críticas:

```env
DATA_DIR=/app/data
LOG_LEVEL=INFO
FERNET_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_USER_ID=
TELEGRAM_BOT_MODE=notifications
ENABLE_EXTERNAL_NOTIFICATIONS=true
MONITOR_INTERVAL_MINUTES=5
MONITOR_AUTOSTART=false
MAX_CONCURRENT_DOWNLOADS=1
GLOBAL_DOWNLOAD_COOLDOWN_MIN_SECONDS=30
GLOBAL_DOWNLOAD_COOLDOWN_MAX_SECONDS=120
YTDLP_ANTIBOT_BACKOFF_BASE_SECONDS=10
YTDLP_ANTIBOT_BACKOFF_CEILING_SECONDS=120
DOWNLOAD_FORMAT=
DB_BUSY_TIMEOUT_ALERT_THRESHOLD=20
HEARTBEAT_INTERVAL_SECONDS=10
DISK_WARNING_FREE_PERCENT=10
SYSTEM_BACKUP_RETAIN_COUNT=7
MAX_VIDEO_RETRY_COUNT=5
MAX_VIDEO_TOTAL_TIME_SECONDS=900
COOKIE_VALIDATION_URL=
YTDLP_PROXY_URL=
YTDLP_EXTRACTOR_ARGS=
NETWORK_PROBE_URL=
NETWORK_PROBE_INTERVAL_SECONDS=30
NETWORK_PROBE_TIMEOUT_SECONDS=5
NETWORK_OFFLINE_THRESHOLD_CONSECUTIVE_FAILURES=2
```

> El `WEBDAV_*` son variables del sidecar `rclone` (§23.4), NO las lee la aplicación (F-17). Mantenerlas documentadas en el README como variables del entorno del sidecar, **comentadas** en el `.env` de despliegue.

Reglas de validación (fail-fast T25): modo `commands`/`both` sin token o chat → el daemon **no arranca**; `MAX < MIN` en el cooldown → error de configuración; notificaciones habilitadas sin token → error. Todo lo demás (formato de sondas, umbrales de disco, intervals, etc.) se documenta en el README.

#### 23.3.4 Primer arranque — checklist

```bash
# 1. Selfcheck (impersonación TLS + ffmpeg/ffprobe + clave Fernet/cookies)
docker compose exec tikdown-rs tikdown-rs daemon selfcheck

# 2. Importar cookies (el feed de TikTok requiere sesión)
docker compose exec tikdown-rs tikdown-rs cookies add /ruta/cookies.txt
#    --keep-source conserva el archivo fuente (el borrado por defecto es best-effort)

# 3. Verificar la cookie contra la sonda
docker compose exec tikdown-rs tikdown-rs cookies list
docker compose exec tikdown-rs tikdown-rs cookies test 1   # valid | invalid | inconclusive

# 4. Añadir cuentas
docker compose exec tikdown-rs tikdown-rs accounts add @usuario            # archivado puntual
docker compose exec tikdown-rs tikdown-rs accounts add @otro --then-monitor # backfill + monitor

# 5. Backfill (foreground, o --queue para encolar al daemon)
docker compose exec tikdown-rs tikdown-rs backfill run @usuario --queue
docker compose exec tikdown-rs tikdown-rs backfill status @usuario

# 6. Monitor (requiere daemon vivo; arranca detenido por defecto)
docker compose exec tikdown-rs tikdown-rs monitor start
```

El throughput del backfill con los defaults es ~40-150 s/vídeo (cooldown 30-120 s + descarga); reanudable por diseño (cursor + archive) ante reinicios.

#### 23.3.5 Actualización

Flujo oficial (§4.1 — **nunca auto-actualización en caliente**):

```bash
# 1. Bump del pin de yt-dlp en pyproject.toml a la nightly deseada:
uv add "yt-dlp[default,curl-cffi,pin-curl-cffi]==<YYYY.MM.DD.HHMMSS>" --prerelease=allow
# 2. Reconstruir y redesplegar
docker compose build && docker compose up -d
# 3. El arranque ejecuta el selfcheck completo: si falla tras el bump, la causa
#    probable es la nueva versión (regresión) → la alerta lo dice explícitamente
docker compose logs tikdown-rs | grep -i selfcheck
```

El daemon detecta versiones nuevas (job 24 h, GitHub API con ETag) y notifica una sola vez por versión (`monitor.yt_dlp_update_available`).

#### 23.3.6 Backups

```bash
# Snapshot consistente en caliente de la base (VACUUM INTO — seguro bajo WAL)
docker compose exec tikdown-rs tikdown-rs system backup
#  → DATA_DIR/backups/tikdown-rs-<fecha>.db

# Y el backup CRÍTICO: la clave Fernet (fuera del volumen y del repo)
cp <DATA_DIR>/fernet.key <destino-seguro>/
```

**Sin `fernet.key` no hay recuperación de cookies**: la única salida es purgar la tabla `cookies` y reimportar cookies frescas (ver §23.5.2).

**Rotación de backups *[Añadido en el análisis posterior]***: `system backup` (§3, §23.5.1) crea un snapshot nuevo en cada ejecución pero el plan no definía qué pasa con los antiguos — sin retención, `DATA_DIR/backups/` crece sin límite y compite por el mismo disco que los propios vídeos que el sistema ya protege activamente contra ENOSPC (T45, §4.4). Regla: `system backup` conserva por defecto los **7 snapshots más recientes** (`SYSTEM_BACKUP_RETAIN_COUNT`, configurable, §12) y borra los más antiguos al crear uno nuevo — la propia base de negocio (`tikdown-rs.db`) es la fuente de verdad operacional; los backups son una red de seguridad puntual, no un histórico completo que deba crecer indefinidamente. El job de disco (§5.1) también contabiliza `backups/` en `system disk`.

**Restauración desde un backup, no solo su creación *[Añadido en el análisis posterior]***: el plan documentaba cómo crear un backup pero no cómo restaurarlo — sin este procedimiento, un backup es un archivo que nadie ha probado a usar hasta que ya es tarde. Procedimiento (con el daemon detenido, para evitar escribir sobre una base en uso):
```bash
docker compose stop tikdown-rs
cp <DATA_DIR>/backups/tikdown-rs-<fecha>.db <DATA_DIR>/tikdown-rs.db
rm -f <DATA_DIR>/tikdown-rs.db-wal <DATA_DIR>/tikdown-rs.db-shm   # WAL/SHM huérfanos del estado anterior
docker compose start tikdown-rs
docker compose exec tikdown-rs tikdown-rs daemon selfcheck        # confirma que la base restaurada es usable
```
Nota importante: un backup restaura la base de datos (cuentas, estado de vídeos, cursor de backfill, cookies cifradas) tal como estaba en el momento del snapshot, pero **no** borra ni reconcilia los vídeos ya descargados después de esa fecha en `<DATA_DIR>/videos/` — quedan en disco sin fila correspondiente en `videos` hasta el próximo `monitor`/`backfill`, que los detectará como nuevos y los reintentará si el `download_archive.txt` no los tiene (el archive, al no formar parte del `.db`, sigue reflejando el estado real de descargas y evita redescargas duplicadas en la mayoría de los casos — revisar `videos integrity` tras restaurar).

#### 23.3.7 Acceso a los vídeos (opcional)

- **Media server**: apuntar Jellyfin (o Plex) a `<DATA_DIR>/videos/`.
- **WebDAV** (sidecar ligero): `rclone serve webdav` — ver plantilla en §23.4.

### 23.4 Sidecar WebDAV (opcional, §17)

`rclone serve webdav` como contenedor adicional (solo lectura, con auth):

```yaml
# docker-compose.override.yml
services:
  webdav:
    image: rclone/rclone:latest
    command: serve webdav /data/videos --addr :8080 --read-only \
      --user ${WEBDAV_USER} --pass ${WEBDAV_PASSWORD}
    volumes:
      - ./data/videos:/data/videos
    restart: unless-stopped
```

Reglas (§22.5): solo lectura siempre; autenticación obligatoria; detrás de reverse proxy con HTTPS o VPN (Tailscale/WireGuard); **nunca expuesto a internet**. La raíz servida debe coincidir con `<DATA_DIR>/videos` (T8). La aplicación NO lee `WEBDAV_*` (F-17): son variables del sidecar.

### 23.5 Operación diaria

#### 23.5.1 Comandos útiles

| Comando | Uso |
|---|---|
| `daemon status` | Heartbeat, monitor, último selfcheck, tareas supervisadas activas, hilos zombis, contención SQLite (5 min) |
| `daemon healthcheck` | exit 0/1 según frescura del heartbeat (para HEALTHCHECK/cron) |
| `daemon selfcheck` | Verificación completa bajo demanda (exit 0/1) |
| `system disk` | Espacio libre, umbral y `downloads_paused`; `--resume` para forzar reanudación tras ENOSPC |
| `system backup` | Snapshot VACUUM INTO de la base |
| `videos integrity [usuario]` | Tamaño + SHA-256 + ffprobe contra lo registrado |
| `videos last` / `videos export` | Consulta/exportación de metadatos |
| `backfill retry-failed @user` | Reintenta vídeos fallidos (descarta el archive antes, T24) |
| `cookies add --keep-source` | Importa conservando el archivo fuente |
| `cookies test <id>` | Prueba una cookie contra la sonda |

#### 23.5.2 Recuperación de la clave Fernet perdida

1. Detener el daemon.
2. Purgar la tabla de cookies (SQLite): `DELETE FROM cookies;` (o `sqlite3 $DATA_DIR/tikdown-rs.db 'DELETE FROM cookies;'`).
3. Reimportar cookies frescas (`cookies add`).
4. Reiniciar. El `daemon selfcheck` detecta una clave rotada antes de que los síntomas aparezcan como fallos de auth aleatorios (T16).

#### 23.5.3 Resolución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| `daemon run` aborta con "ffmpeg no encontrado" | ffmpeg/ffprobe ausentes (T46) | Instalar ffmpeg en el host/imagen |
| Selfcheck: "Sin targets de impersonación" | curl-cffi ausente, versión no soportada o plataforma ARM64 (T6) | Seguir el mensaje: `pin-curl-cffi`, `yt-dlp --list-impersonate-targets`, probar amd64 |
| Selfcheck: "clave Fernet activa no puede descifrar" | Clave rotada/incorrecta (T16) | Purgar cookies + reimportar con la clave correcta |
| `daemon healthcheck` = unhealthy con daemon vivo | Heartbeat stale (proceso colgado o intervalo cambiado) | Frescura = ≤ 3 × `HEARTBEAT_INTERVAL_SECONDS`; revisar logs |
| Vídeos `failed` con "status code 0" | Respuesta degradada/anti-bot, NO contenido inexistente (T53) | Transitorio: se reintenta; si persiste, `retry-failed` |
| "keeps sending the same page" en logs | Feed degradado; el extractor reintenta solo (T54) | Transitorio: no requiere acción |
| 403 sin "login required" | Bloqueo por ritmo, no por credenciales (T5) | Transitorio: el cooldown/backoff lo gestiona; no invalidar cookies |
| `downloads_paused=SÍ` | Disco lleno (ENOSPC, T45) | Liberar espacio; el job de disco reanuda solo; o `system disk --resume` |
| Backfill "no avanza" | Pacing global (30-120 s/vídeo) o slot ocupado | `backfill status`; la recogida de `queued` corre cada 30 s |
| "cookie.validation_probe_failed" | Sonda `COOKIE_VALIDATION_URL` muerta/renombrada (T57) | Cambiar la sonda a un perfil público estable y reiniciar |
| `cookies test N` → `inconclusive` — `No video formats found` con cookie válida | La sonda tiene el embedding deshabilitado o su primera entrada es un slideshow (T74): el primer vídeo del feed no devuelve formatos | Cambiar `COOKIE_VALIDATION_URL` a un perfil verificado: `yt-dlp -s <perfil>` debe devolver formatos para su primer vídeo antes de desplegarlo (default del código: **`@rosary657`**) |
| `cookies test N` → `inconclusive` — `does not look like a Netscape format cookies file` | Blob cifrado antiguo sin el header mágico Netscape (T73) | Reimportar las cookies (el fix regenera el header automáticamente; un blob viejo puede requerir reimportación) |
| Monitor detenido sin cookies | `monitor.stopped_no_cookies` (§5.3) | Importar cookies válidas |
| "Contención SQLite alta" | Bloqueos concurrentes en la base | Normal bajo backfill+monitor; revisar si supera el umbral sostenido |
| Notificaciones no llegan | `ENABLE_EXTERNAL_NOTIFICATIONS=false` o chat no autorizado | Revisar `.env` y `bot.unauthorized_attempt` en logs |
| Bot no responde; log `Conflict: terminated by other getUpdates request` | Otra sesión de `getUpdates` compitió (p. ej. diagnóstico manual): la Bot API solo permite UNA sesión simultánea y el polling del bot muere sin que el daemon lo detecte (T71) | Reiniciar el contenedor. **Nunca** usar `getUpdates` manual contra un bot en polling: verificar con `getMe`/`sendMessage` (no compiten) |
| `docker logs` con 0 bytes y daemon healthy | `fileConfig()` de Alembic pisó el root logger (T72) | Reaplicación de logging tras migrar (ya en el código del arranque, §5.1); actualizar la imagen |
| CI falla en 0s (`startup_failure`) | Billing o configuración del runner de Woodpecker CI (L-K4) | Verificar el estado del runner en el panel de Woodpecker y resolver el billing/configuración; no es un problema de código |

#### 23.5.4 Notificaciones (opcional)

Con `ENABLE_EXTERNAL_NOTIFICATIONS=true`, el daemon notifica: vídeos nuevos (opcional por cuenta), fallos de descarga (siempre, con URL y siguiente paso), cookies inválidas, sonda rota, caída/recuperación de red, disco, actualizaciones de yt-dlp y el estado del backfill. En ráfagas de descargas la notificación se agrupa en un resumen único (coalescing, §8). Durante una caída de red los eventos se persisten en el spool y se entregan al volver (T42).

### 23.6 FAQ

**¿Puedo correr el CLI sin el daemon?** Sí: los comandos de un solo disparo (accounts, backfill foreground, cookies, videos, system) funcionan solos contra el mismo `DATA_DIR`. Solo `monitor start/stop`, el encolado de backfills (`--queue`) y las descargas en segundo plano requieren el daemon vivo.

**¿Puedo tener el daemon en Docker y usar el CLI en el host?** No directamente: el CLI debe apuntar al mismo `DATA_DIR` (con `docker compose exec` o montando el mismo volumen en un contenedor de comandos).

**¿Cuánto tarda un backfill?** Con los defaults, 40-150 s por vídeo. 1.000 vídeos ≈ 11-42 h. Es reanudable por diseño: se puede detener y retomar.

**¿Qué pasa si TikTok cambia su frontend?** El pin a una nightly reciente de yt-dlp minimiza el impacto; el monitor avisa de nuevas versiones. Si un extractor se rompe, los fallos se clasifican como transitorios (nunca se invalidan cookies por error del extractor).

**¿Qué hago si pierdo `fernet.key`?** Ver §23.5.2: purgar `cookies` y reimportar. No hay otra recuperación.

**¿La CI está activa?** La pipeline de Woodpecker CI (`.woodpecker.yml`) puede estar deshabilitada o el runner caído por billing o configuración (L-K4) — verificar el estado en el panel de Woodpecker y el log de la pipeline. El pre-commit local (ruff) sigue activo.

---

## 24. Convenciones de trabajo (código, revisión, errores, herramientas y git)

> Esta sección define **cómo se trabaja** en este proyecto: código, revisión, errores, herramientas y flujo git. Las secciones §0–§18 definen *qué* se construye; esta define *cómo*.

### 24.1 Reglas de oro del proyecto

*[Añadido en la consolidación: esta subsección estaba declarada pero vacía en la documentación anterior — «Reglas de oro» sin contenido; se reconstruye a partir de los principios dispersos en los documentos originales. Ver §25.]*

1. **Una sola capa de lógica de negocio**: `services/*`. `cli/`, `daemon/` y el bot de Telegram solo orquestan llamadas a servicios. Prohibido duplicar lógica de negocio en las interfaces (plan §13).
2. **DRY real, no dogmático**: no repetir lógica. Si la duplicación deliberada es más barata y más clara que el acoplamiento que introduciría una abstracción, se permite y se documenta con un comentario.
3. **Sin sobre-ingeniería (YAGNI)**: nada de capas, parámetros ni dependencias que el plan no pida. Una abstracción solo cuando hay 2+ usos reales. La estructura de módulos es la del plan §13 — no crear módulos nuevos sin justificación.
4. **Async-first con disciplina**: toda I/O pesada (SHA-256, ffprobe, fsync, globs, yt-dlp) va a `asyncio.to_thread` (T12). Toda tarea de fondo pasa por `create_supervised_task()` — nunca `asyncio.create_task` directo (principio 10). El `add_done_callback` debe ser síncrono (T1).
5. **Sesiones cortas de DB**: abrir → trabajo estricto → cerrar. Nunca mantener una sesión abierta durante I/O de red (T32). Los helpers mutadores de `daemon_state` commitean internamente (T37).
6. **Fallos clasificados, nunca excepciones a pelo**: usar `core/errors.py` (3 estados, plan §4.3) en toda la cadena. Nunca marcar `downloaded` sin pasar por `handle_download_result` (plan §4.6).
7. **Comentarios de "por qué"**, no del "qué". Citar la trampa del plan cuando aplique, p. ej. `# T24: descarte del archive antes del reintento`.
8. **Nombres precisos y en inglés** (código y commits, como el ecosistema): verbos para funciones, sustantivos para tipos, sin abreviaturas ambiguas.
9. **Logging stdlib**: el stack NO usa `structlog` (decisión F-20; el daemon usa logging stdlib con formatter JSON ad-hoc). No reintroducir dependencias de logging sin justificación y aprobación de auditoría.

### 24.2 Revisión de código

- La revisión se hace **contra las trampas T## del plan**, no solo contra el código: cada cambio se contrasta con la checklist de §19 y con las lecciones de §21.
- Un hallazgo de revisión se resuelve en el mismo turno, o se registra explícitamente como deuda con entrada en el §21 del plan si es un error latente.
- La suite local (`uv run pytest tests/`) se ejecuta en la rama antes de abrir una PR, y el PR se revisa idealmente con `gh pr diff` + la checklist de trampas.

### 24.3 Flujo ante errores y dudas técnicas (obligatorio)

**Ante cualquier error o duda — nunca adivinar la causa**: [Añadido en la consolidación: la frase original estaba cortada aquí; se reconstruye el flujo completo a partir de las reglas dispersas en la documentación anterior. Ver §25.]

1. **Reproducir y clasificar primero**: ¿es un fallo transitorio/de red, un error de configuración, o un error de código? Un fallo transitorio o de red **nunca** se registra como defecto de código, ni invalida cookies ni consume reintentos (principio 11, T64).
2. **Consultar el plan antes de tocar código**: si la sección relevante (§0–§18) ya define el comportamiento, esa es la verdad; si la trampa T## o la lección §21 correspondiente ya documenta el caso, aplicar su regla directamente.
3. **Si es un error de código**: resolverlo con test de regresión primero (TDD, §14), y registrar **siempre** la entrada en §21 (mismo turno).
4. **Si el error revela una trampa nueva** no documentada → añadirla a §19/§20 como enmienda, además del registro en §21.
5. **Duda de diseño/alcance** → el plan manda; si el plan no la resuelve, decidir por sus 5 prioridades finales (§26: simplicidad homelab → reutilización de `services/*` → fail-fast → sin secretos → UX terminal/Telegram).

### 24.4 Herramientas de desarrollo (resumen)

- **`uv`** (≥0.12): `uv sync`, `uv run`, `uv lock` (pins según §1.1).
- **`ruff`**: `ruff check && ruff format` — pre-commit (`.pre-commit-config.yaml`) y CI.
- **`pytest` + `pytest-asyncio` + `coverage`**: suite completa con mocks; nunca llamadas reales a TikTok (§14).
- **`gh` CLI** para PRs y `sqlite3` para inspección ad-hoc de la base.

### 24.5 Flujo de trabajo con ramas y PRs (git)

Regla de oro: **nunca se modifica `main` directamente** — todo cambio entra por un PR desde la rama de trabajo (`vibe`). `main` es la rama estable; el historial debe quedar limpio de secretos antes del primer push público (§22.3).

#### 24.5.1 El ciclo de trabajo

Regla de oro: **nunca se modifica `main` directamente** — todo cambio entra por PR desde la rama de trabajo (`vibe`). `main` es la rama estable; el historial debe quedar limpio de secretos antes del primer push público (§22.3).

```bash
# 1. Partir siempre de main actualizado
git fetch origin
git checkout main && git pull

# 2. Trabajar en vibe (rama de integración)
git checkout -b vibe    # rama nueva; si existe:
git checkout vibe
git reset --hard origin/main     # vibe = espejo de main + tu trabajo nuevo
git push --force-with-lease origin vibe   # solo si ya existía en remoto
# ... codificar, probar (pytest + ruff), commitear (Conventional Commits) ...

# 3. Push de la rama de trabajo para que la CI lo valide
git push origin vibe

# 4. Abrir la PR hacia main
git switch main            # volver a main primero
git pull                   # asegurar main actualizado
git checkout vibe          # volver a la rama de trabajo
# (resolver conflictos aquí si main avanzó: git rebase main)
# git push --force-with-lease origin vibe
# gh pr create --base main --head vibe --title "..." --body "..."

# 5. La CI de Woodpecker (.woodpecker.yml) ejecuta ruff, pytest, cobertura y
#    build+smoke Docker en cada push a vibe y en la PR

# 6. Mergear en main con SQUASH (historial limpio, un commit por PR)
# 7. Repetir el ritual: reset de vibe a main
git checkout main && git pull
git branch -f vibe origin/main
git push --force-with-lease origin vibe   # vibe = espejo de main
```

#### 24.5.2 Modos de merge y por qué el squash exige el ritual de reset

El flujo Genérico de Git (no atado a una plataforma) admite tres modos de merge comunes, todos con la misma implicación sobre ramas de trabajo:

| Modo | Efecto en `main` | ¿Conflicto en la siguiente PR desde la misma rama? |
|---|---|---|
| **Create a merge commit** | Conserva todos los commits del PR + un commit de merge | No — los commits originales pasan a `main`; la siguiente PR solo muestra lo nuevo |
| **Squash and merge** (recomendado) | Fusiona el PR en UN solo commit | **Sí, si no se resetea la rama** — los commits originales nunca entran en `main` y la siguiente PR los re-aplica (conflicto real sufrido en la implementación anterior: marcadores `<<<<<<<` en documentos y tests) |
| **Rebase and merge** | Reescribe los commits del PR sobre la punta de `main` | No en la práctica — el contenido ya está en `main` |

**Modo recomendado: `Squash and merge` siempre** (historial de `main` limpio, un commit por PR, coherente con Conventional Commits) **+ el ritual de reset de §24.5.1 tras cada merge**. Alternativa sin ritual: `Create a merge commit` (historial más ruidoso, sin conflictos estructurales).

**Regla: no mezclar modos de merge** sobre la misma rama larga — es la receta para conflictos difíciles de explicar.

#### 24.5.3 Si una PR ya tiene conflictos

La causa típica es la del §24.5.2 (squash sin reset). Resolución limpia: reconstruir la rama sobre `main` rejugando solo los commits NO mergeados, descartando los que el squash ya absorbió:

```bash
# Conocer los commits de la rama que YA están en main (vía squash):
git log --oneline origin/main..vibe
# Rebase quirúrgico: rejuega sobre main solo los commits posteriores al último absorbido
git fetch origin
# git rebase --onto origin/main <sha-del-último-commit-ya-mergeado> vibe
# git push --force-with-lease origin vibe
```

Verificar que el árbol no perdió contenido: `git diff <sha-pre-rebase> vibe --stat` debe estar vacío.

#### 24.5.4 Protección de ramas (flujo genérico)

La protección de ramas depende de la plataforma Git que se use (GitHub, GitLab, Forgejo, etc.). En cualquier caso, el gate de calidad **no es opcional**:

- **CI obligatoria en verde**: Woodpecker CI (`.woodpecker.yml`) debe aprobar `ruff check`, `ruff format` (verificación de formato), la suite de pytest con su cobertura objetivo, y el build+smoke Docker antes de que un PR se pueda mergear a `main`.
- **Revisión obligatoria**: idealmente con `git diff main..vibe` y la checklist de trampas T## del plan, y ejecutar la suite local (`uv run pytest tests/`) en la rama antes de abrir la PR.
- **Squash and merge**: historial de `main` limpio, coherente con Conventional Commits.
- **Historial limpio de secretos**: si un secreto llegó a commitearse, reescribir el historial (`git filter-repo`) antes del primer push público (§22.3).

> **Nota sobre GitHub específicamente**: en repositorios privados con plan free, GitHub no permite reglas de protección de ramas (exige Pro o repo público). Si se usa GitHub, el gate es **disciplina**, no enforcement — pero Woodpecker CI como gate de calidad sigue siendo posible configurarlo vía protected builds o mediante la integración de Woodpecker con GitHub (el `.woodpecker.yml` no depende de GitHub Actions; es una pipeline independiente que se dispara en push/PR).

---

## 25. Registro de contenido añadido o reconstruido durante la consolidación

> **Qué es esta sección**: la documentación anterior llegó a la consolidación con fragmentos rotos, frases cortadas y omisiones (por intervención humana que no supo reordenar los textos, y por eliminaciones deliberadas de algunos pasajes). Este registro documenta **todo lo que el consolidador añadió o reconstruyó**, para que el implementador sepa qué es contenido original de la documentación previa y qué es reconstrucción/adición. Nada de lo aquí listado cambia el comportamiento especificado en las secciones normativas (o lo hace solo como aclaración marcada como tal).

### 25.1 Fragmentos rotos reconstruidos (marcados inline como *[Añadido en la consolidación]*)

| # | Dónde | Qué estaba roto | Qué se reconstruyó |
|---|---|---|---|
| R1 | §24.1 (Reglas de oro) | El bloque «Reglas de oro» de las convenciones estaba **vacío** (solo el título, sin contenido) | Se reconstruyeron las 9 reglas de oro a partir de los principios de código dispersos en las convenciones originales y del plan (§13, principio 10, §4.3, §4.6, T1, T12, T32, T37, F-20) |
| R2 | §24.3 (Flujo ante errores) | La frase «Ante cualquier error o duda — nunca adivinar…» estaba **cortada** sin completar | Se reconstruyó el flujo completo (reproducir/clasificar → consultar plan → resolver con test → registrar en §21 → decidir por prioridades) a partir de las reglas de error dispersas en las convenciones originales y de las lecciones de §21.11 |
| R3 | §1.2 (Procedimiento de reverificación) | El procedimiento estaba referenciado («reproducir el procedimiento documentado») pero **nunca escrito** | Se reconstruyó el procedimiento paso a paso a partir de las notas de verificación dispersas en §1/§1.1 (R3.1: aclaración) |

### 25.2 Secciones del documento anterior que se fusionaron (no eliminadas)

| Documento anterior | Sección de destino en este plan maestro |
|---|---|
| Especificación canónica (plan) | §0–§18 (numeración preservada íntegra), §19 (trampas), §20 (decisiones), §21 (lecciones) |
| Convenciones de trabajo | §24 (convenciones y flujo git) |
| Política de seguridad | §22 |
| Guía de despliegue | §23 |

**Nada del contenido de los 4 documentos se eliminó**: se reordenó, reformuló y fusionó. Las únicas bajas son (1) la cronología de enmiendas del §20 histórico (reemplazada por la decisión vigente y su justificación — el contenido decisional está preservado), y (2) las referencias cruzadas a archivos que ya no existen (los 4 documentos históricos), sustituidas por referencias internas a las secciones de este documento.

### 25.3 Contradicciones resueltas (decisión del consolidador, validada por el propietario)

| # | Contradicción | Resolución |
|---|---|---|
| C1 | **Sonda de cookies por defecto**: el plan §7/§12 y la guía de troubleshooting decían `@dakpept`; la guía §3.3 y la enmienda de decisión decían `@rosary657` | **Default consolidado: `@rosary657`** (perfil verificado en producción con formatos extraíbles). Se conserva además la robustez de la sonda que itera las primeras 5 entradas (L-E4/T74), que funciona con cualquier sonda |
| C2 | El plan §13 afirmaba que `docs/` contenía «exactamente 4 documentos» | Reformulado: `docs/` contiene **un único documento normativo** (este plan maestro) (§13) |
| C3 | Referencias a `CONVENTIONS.md §5`, «guía de despliegue §6.2», etc. | Internalizadas como referencias a secciones de este documento (§1.1/§1.2, §23.5.2, …) |

### 25.4 Aclaraciones del consolidador (sin cambio de comportamiento)

| # | Dónde | Aclaración |
|---|---|---|
| N1 | §1.1 | Nota de que la tabla de versiones era el último estado verificado (2026-08-03) y debe reverificarse (§1.2) |
| N2 | §13 | Comentarios en el árbol de estructura que señalan dónde vive cada lección/trampa (sin cambiar la estructura) |
| N3 | §2 | Nota explícita de que el CHECK constraint de `backfill_status` incluye `'cancelled'` desde el primer esquema (requisito verificado por la lección L-F7) |
| N4 | §4.5/§2 | Nota explícita de los dos detalles críticos del pacing cross-proceso (commit del singleton L-C6 + milisegundos L-C7) |
| N5 | §9 | Nota de que el evento de red por defecto del motor se crea seteado (L-D2) |
| N6 | §3/§4.6/§6.4/§8 | Notas de lecciones (L-A6, L-G2, L-G3, L-H6, L-H7, …) que indican dónde aplica cada regla |
| N7 | §20 | La sección de decisiones se reorganizó por temas (no por cronología); el contenido decisional de las enmiendas históricas se preservó íntegro en forma de decisión vigente + justificación |
| N8 | §23.3.3 | El bloque `.env` de la guía se alineó con §12 (añadido `TELEGRAM_USER_ID` y comentario de `COOKIE_VALIDATION_URL` = `@rosary657`) |

### 25.5 Correcciones de consistencia posteriores (revisión externa 2026-08-19)

Revisión de consistencia interna y de verificación externa de versiones. Nada de lo listado cambia el comportamiento especificado; son correcciones de referencias y adiciones defensivas justificadas:

| # | Tipo | Cambio |
|---|---|---|
| R4 | Referencia roto | 5 citas a `§23.6` (FAQ) corregidas a `§23.5.2`, donde vive realmente el procedimiento de recuperación de `fernet.key` (§0.1, §13, §20.7, §22.3, §23.3.6) |
| R5 | Lecciones colgadas | L-F8 → L-F7 (el contenido citado vive en L-F7), L-I2 → L-B5 (ídem), L-K1/L-K2 → L-K1 (L-K1 cubre ambos problemas del Dockerfile). Las tres citaban IDs sin fila en §21 |
| R6 | Reestructuración | Las "5 prioridades finales" — citadas desde §20.9, §21.11 y §24.3 como «el cierre de este documento» — ahora tienen sección numerada propia: §26 |
| R7 | Adición de seguridad | Nota en §5.4 sobre CVE-2026-31072 (deserialización insegura en serializadores JSON/CBOR de APScheduler): el vector no aplica al proyecto por el uso de `MemoryJobStore`, y la nota blinda la decisión frente a futuras propuestas de jobstore persistente |
| R8 | Adición de despliegue | Requisito explícito en §23.2: `DATA_DIR` no puede residir en NFS/SMB (SQLite WAL no garantiza locks ni `mmap` sobre sistemas de archivos de red); un NAS sirve para backups o para servir vídeos, no para la base viva |
| R9 | Ambigüedad resuelta | §2/§5.2/§10: se define el productor de reintento de los vídeos `status='cancelled'` — no es estado terminal para el cursor, no entra en el archive y no lo recoge `retry-failed`; lo reintenta la re-ejecución del backfill (re-encolado por `CancelledError` + reconciliación de arranque, F-10) o la diferencia de conjuntos del monitor. La frase «se reintentan al reiniciar» de §5.2 tenía el mecanismo real pero sin productor nombrado |
| R10 | Ajuste operativo | §3/§5.5: `daemon healthcheck` y `--version` quedan exentos de ejecutar migraciones y de tomar `.migrate.lock` — el `HEALTHCHECK` de Docker corre cada ~30 s y el churn del lock contra el daemon no aportaba nada; el healthcheck abre la base en lectura y reporta unhealthy si el esquema no está al día |
| R11 | Nota de vigencia | §4.1 (L-D1): la exigencia del objeto `ImpersonateTarget` frente a un string es dependiente de versión (hay evidencia de aceptación de strings en nightlies recientes); se manda verificar contra la nightly pineada. La regla de implementación (conservar objetos) no cambia por ser compatible con ambas variantes |
| R12 | Robustez/ética | §7/§12/§14/§20.7: `COOKIE_VALIDATION_URL` pasa a admitir una lista separada por comas con fallback en orden (`cookie.validation_probe_failed` solo cuando todas las candidatas fallan). Se documenta que el default `@rosary657` es una cuenta de terceros publicada en un repo público (punto único de fallo externo; recibe el tráfico de sondas de todos los despliegues) y que el README debe recomendar configurar 2-3 sondas propias |
| R13 | Verificación externa | El extra `pin-curl-cffi` de yt-dlp queda confirmado en índices de dependencias públicos (la nightly 2026.4.10.235301.dev0 declara `curl-cffi==0.15.0` bajo ese extra, y la página del proyecto en PyPI lista `pin-curl-cffi` entre los extras documentados); coherente con §1.1. El paso 2 de §1.2 (verificar el extra en el pyproject upstream de la nightly elegida) sigue siendo obligatorio — la tabla §1.1 es un estado verificado, no un mandato ciego |

### 25.6 Contenido que el consolidador consideró añadir y NO añadió (a petición de no inventar comportamiento)

| Candidato | Decisión |
|---|---|
| Añadir una sección de "arquitectura de directorios de código con más detalle" | **No añadido**: el plan ya define la estructura completa en §13; añadir más detalle especulativo violaría la regla de no sobre-especificar. |
| Añadir un esquema SQL completo con DDL | **No añadido**: el plan §2 define los campos/constraints y delega el DDL exacto al implementador (como hace el original). |
| Añadir un ejemplo de `docker-compose.override.yml` con red VPN | **No añadido**: la guía ya incluye la plantilla WebDAV; el resto es decisión de despliegue del operador. |

### 25.7 Adiciones de un análisis posterior (2026-08-24), marcadas inline como *[Añadido en el análisis posterior]*

> Revisión independiente del plan maestro ya consolidado, enfocada en huecos operativos que sobreviven al MVP tal y como estaba especificado: ninguna de estas adiciones cambia el comportamiento normativo existente ni contradice ninguna decisión de §20; todas son extensiones aditivas a huecos genuinamente no cubiertos. Ninguna introduce una dependencia de servicio externo nueva (Redis, bots de PRs automáticos, frontend), en línea con el principio de simplicidad homelab (§26).

| # | Dónde | Qué añade | Motivo |
|---|---|---|---|
| A1 | §0.1, §13 | Archivo `LICENSE` (recomendación: MIT) | El objetivo declarado es "publicable... como proyecto open-source" pero ningún archivo fijaba una licencia; sin ella el repo público es "todos los derechos reservados" por defecto |
| A2 | §1.3, §22.6, T76 | Job semanal de CI (`pip-audit` + `trivy image`), separado del pipeline de push/PR | Los pines exactos (§1, §22.6) protegen de romper compatibilidad, no de una CVE publicada después de fijar el pin; sin comprobación periódica una dependencia "estable" puede acumular meses de vulnerabilidad conocida sin que nadie lo note |
| A3 | §11 | Rotación de logs Docker (`max-size`/`max-file` en `docker-compose.yml`) | El logging JSON a stdout (principio 8) no tenía techo de crecimiento; el proyecto ya trata el disco lleno como caso de primera clase para vídeos y DB (T45) pero los logs de Docker quedaban fuera de ese monitoreo |
| A4 | §23.3.6, §12, §3 | Retención de backups (`SYSTEM_BACKUP_RETAIN_COUNT`, default 7) + procedimiento explícito de restauración | `system backup` no tenía límite de retención (mismo riesgo de disco que A3); y el plan documentaba crear backups pero nunca cómo restaurarlos — un backup no probado es un backup que no se sabe si funciona |
| A5 | §22.4, §8 | Alerta agregada `bot.unauthorized_attempts_burst` ante ≥5 intentos no autorizados en 5 min desde el mismo `from_user.id` | `bot.unauthorized_attempt` ya auditaba cada intento individual, pero no distinguía un intento aislado de un patrón de tanteo sostenido; sin estado nuevo en base de datos ni mecanismo de baneo (fuera de alcance del MVP) |
| A6 | §22.4 | Procedimiento de rotación de `TELEGRAM_BOT_TOKEN` | El plan documentaba backup/recuperación de `fernet.key` pero no qué hacer si el token del bot se filtra o se quiere rotar preventivamente |

---

## 26. Prioridades de resolución de ambigüedades

**Fin del plan maestro.** Este documento está listo para que una IA implementadora construya el proyecto completo (CLI + daemon + bot Telegram) desde cero, sin necesidad de contexto previo ni de ningún otro documento.

Cualquier ambigüedad debe resolverse priorizando, en este orden:
1. Simplicidad y fiabilidad para uso homelab de un solo usuario.
2. Reutilización estricta de la capa `services/*`.
3. Fail-fast en condiciones críticas (impersonación, cookies, configuración faltante).
4. Ningún secreto real en el repositorio, en ningún punto del historial, ni en ninguna capa de imagen Docker.
5. Buena experiencia de usuario en terminal y en Telegram.

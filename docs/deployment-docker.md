# Guía de despliegue en Docker — tikdown-rs

Guía de operación completa para desplegar el daemon en Docker, basada en el
`Dockerfile` y el `docker-compose.yml` reales del repo. La referencia normativa es
`specs/Plan de implementacion tikdown-rs.md` §14 (cada decisión dice su origen:
`T-<ÁREA>-N` = trampa verificada en Apéndice A, `DR-N` = decisión de diseño).

Docker es el **único entorno de producción** del proyecto (§14.1).

## 1. Requisitos previos

- Docker Engine + Docker Compose v2 (`docker compose version`).
- Aproximadamente 2 GB de disco para la imagen + espacio para la biblioteca de vídeos.
- Una `/ruta/cookies.txt` en **formato Netscape** (exportada de una sesión TikTok
  con sesión iniciada). El parser rechaza archivos sin el header Netscape
  `# Netscape HTTP Cookie File` (T-COOKIES-1) — no vale "arreglarlo a mano".

> **Plataforma**: antes de comprometerse con ARM64, verificar que existan wheels
> de `curl-cffi` para la combinación exacta de versiones y que el selfcheck pase
> (§14.3). Si no hay targets, usar imagen `amd64` por emulación o hardware amd64.

## 2. La imagen, en 60 segundos

Es un build multi-stage con el patrón oficial de `uv`:

1. **stage `builder`** (`python:3.13-slim` + binarios de `uv`): instala solo las
   dependencias con `uv sync --frozen --no-editable --no-install-project` **antes**
   de copiar el código — así la capa de dependencias queda cacheada y cambiar
   `src/` no reinstala nada.
2. **stage `runtime`** (`python:3.13-slim`): instala `ffmpeg` necesario para el
   post-procesado de yt-dlp (T-DEPLOY-10), crea usuario no-root `tikdown`, copia el
   `.venv` + **los recursos no Python explícitamente** (`alembic.ini`, `alembic/`) —
   sin eso el primer arranque muere con `FileNotFoundError: alembic.ini`
   (T-DEPLOY-5, T-DEPLOY-23).

`README.md` viaja en el build porque hatchling lo exige para armar el wheel
(T-DEPLOY-11). Por eso `.dockerignore` lo re-incluye.

**El CMD es `["tikdown-rs", "daemon", "run"]`, verificado contra el árbol real de
comandos de typer con un test (`tests/test_docker_config.py`), no a ojo (T-CLI-5).**

## 3. Despliegue paso a paso

### Paso 1 — configuración

```bash
git clone <repo> tikdown-rs && cd tikdown-rs
cp .env.example .env
```

`.env` **no** se commitea (`.gitignore` lo bloquea; `docker-compose.yml` lo carga
con `required: false`, así el build no explota si aún no existe). Las mínimas para
arrancar:

| Variable | Obligatoria | Para qué |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | sí | notificaciones al bot |
| `TELEGRAM_CHAT_ID` / `TELEGRAM_USER_ID` | sí | destinatario + control de autorización del bot |
| `DATA_DIR` | no | ya va apuntada a `/app/data` en la imagen |
| `MONITOR_AUTOSTART` | no | arranca el monitor con el daemon (`true` recomendado si ya tenés cuentas) |
| `YTDLP_PROXY_URL` | no | proxy **residencial** si lo necesitás (§14.7: un proxy de datacenter empeora el WAF) |

Todo el `.env.example` está sincronizado contra `Settings` con un test (T-DATA-4):
no hay variables muertas ni documentación faltante.

### Paso 2 — levantar

```bash
docker compose up -d --build
```

Al primer arranque el contenedor corre las **migraciones de Alembic** y el
daemon arranca con heartbeat. El `HEALTHCHECK` usa
`tikdown-rs daemon healthcheck` (liviano, sin red, no migra — T-DEPLOY-1) con
`--start-period 60s` porque las migraciones corren en el primer boot.

### Paso 3 — verificar el primer arranque

```bash
docker compose exec tikdown-rs test -f /app/alembic.ini    # los recursos llegaron al runtime (T-DEPLOY-5)
docker compose exec tikdown-rs tikdown-rs daemon selfcheck # verificación completa: config, engine, disco, ffmpeg/ffprobe
docker compose logs -f tikdown-rs                          # "docker logs" en 0 bytes = bug del logger, ya corregido (T-DEPLOY-6)
```

Si el selfcheck falla, mirá primero §8 de esta guía y las tablas de síntomas del
spec §14.6 antes de tocar nada: un fallo transitorio no es un defecto.

### Paso 4 — cookies (por CLI, no por el bot)

```bash
docker compose cp cookies.txt tikdown-rs:/tmp/cookies.txt
docker compose exec tikdown-rs tikdown-rs cookies add /tmp/cookies.txt
docker compose exec tikdown-rs tikdown-rs cookies test 1
docker compose exec tikdown-rs rm /tmp/cookies.txt
```

- **Por qué CLI**: por el bot solo conviene importar archivos pequeños; los
  sensibles van por CLI (§6.3). Además el mensaje se borra best-effort si venía
  por bot — por CLI es limpio.

- **Por qué `cookies test`**: la sonda valida de verdad contra TikTok. Un
  resultado `inconclusive` con cookie válida es posible (sonda inalcanzable,
  embedding deshabilitado, primera entrada slideshow) y **no invalida** nada
  (T-COOKIES-2/3/4). Solo un fallo de auth confirmado escribe `invalid`.

### Paso 5 — cuentas y backfill

```bash
docker compose exec tikdown-rs tikdown-rs accounts add @usuario --then-monitor
docker compose exec tikdown-rs tikdown-rs backfill run @usuario --queue
docker compose exec tikdown-rs tikdown-rs monitor start
```

Referencia de rendimiento (§14.7): ~40–150 s por vídeo con pacing realista; un
backfill de 1.000 vídeos ≈ 11–42 h. Es reanudable: si algo se corta, el arranque
del daemon reconcilia backfills huérfanos (T-BACKFILL-6) y `backfill retry-failed`
relanza lo que quedó en failed.

Verificación anti-crash-loop clásica:

```bash
docker compose exec tikdown-rs tikdown-rs daemon stop   # debe devolver exit 0 y PARARSE de verdad
docker compose ps                                        # el contenedor NO debe reiniciarse
```

Por eso el compose usa `restart: on-failure` y **nunca** `unless-stopped`: una
parada pedida por el operador (exit 0) queda parada; un crash (exit ≠ 0) sí se
recupera (T-DEPLOY-13).

## 4. El `docker-compose.yml`, línea por línea

| Bloque | Qué protege |
|---|---|
| `env_file` (`.env`, `required: false`) | secretos sin commit; build no depende del env |
| `volumes: ./data:/app/data` | base, cookies, archive y vídeos en **un solo volumen** (§14.1); guardar DATA_DIR en paths absolutos dentro del volumen (T-BACKFILL-21) |
| `tmpfs: /tmp` | temporales fuera del volumen (los tempfiles de cookies nunca persisten) |
| `security_opt: no-new-privileges:true` | escalada de privilegios imposible |
| `cap_drop: ALL` | cero capabilities; el runtime es un downloader con ffmpeg, no necesita ninguna |
| `restart: on-failure` | parada limpia del operador queda parada; crash se recupera (T-DEPLOY-13) |
| `logging json-file max-size 10m / max-file 5` | rotación: ~50 MB de logs máximo |
| servicio `site` (nginx:alpine, ro) | dashboard estático opcional, ver §7 |

## 5. Dashboard estático (opcional)

```bash
docker compose exec tikdown-rs tikdown-rs system site render
```

Escribe `index.html` + 3 JSONs en `<DATA_DIR>/public`; el container `site` los by
sirve en `:8080` (o `SITE_PORT`). Es un mount **read-only** de archivos
regenerables — ni la base ni las cookies tocándolo. Para automatizar:
`STATIC_SITE_ENABLED=true` + `STATIC_SITE_INTERVAL_MINUTES`.

## 6. Actualizaciones

```bash
git pull
docker compose up -d --build
docker compose exec tikdown-rs tikdown-rs daemon selfcheck
```

**Del yt-dlp bump (T-ENGINE-28)**: después de actualizar la imagen, el diagnóstico
por defecto es el bump. `daemon selfcheck` compara la versión en uso contra
`last_known_good_ytdlp_version`; si difiere, el sospechoso es el extractor, no
TikTok ni tus cookies. Si hay que retroceder, el pin está en `pyproject.toml` y
`uv.lock` lo congela (`uv sync --frozen` en el build).

## 7. Backup y restore

```bash
docker compose exec tikdown-rs tikdown-rs system backup     # VACUUM INTO → <DATA_DIR>/backups/
```

- Retención: `SYSTEM_BACKUP_RETAIN_COUNT` (default 7), archivos `chmod 0600`,
  publicación atómica (stage + `os.replace`) para que nunca veas un snapshot
  parcial.
- Online-safe por diseño (§14.5): corre con el daemon vivo.
- **El backup se trata como secreto**: la base guarda cookies sin cifrar (§15.2).

Restore (con el daemon detenido):

```bash
docker compose exec tikdown-rs tikdown-rs daemon stop
docker compose stop
cp <tu-snapshot>.db ./data/tikdown-rs.db                   # y borrá ./data/tikdown-rs.db-wal/-shm
docker compose start
docker compose exec tikdown-rs tikdown-rs daemon selfcheck
docker compose exec tikdown-rs tikdown-rs videos integrity # diff vs snapshot si querés
```

Los vídeos posteriores al snapshot siguen en disco y el archive evita
redescargas — `videos integrity` revisa tamaño + SHA-256 + ffprobe.

## 8. Síntomas comunes del primer arranque

(`T-ID` = fila completa en Apéndice A del spec)

| Síntoma | Causa |
|---|---|
| crash-loop `No such command 'run'` | CMD desalineado con el árbol real de typer (T-CLI-5); el repo lo previene con test |
| `FileNotFoundError: alembic.ini` | recursos no copiados o COPY relativo antes de `WORKDIR /app` (T-DEPLOY-5/23); el repo lo previene con test |
| `daemon healthcheck` unhealthy con daemon vivo | heartbeat stale > 3 × `HEARTBEAT_INTERVAL_SECONDS` (T-DEPLOY-7): mirá carga, no reinicies en pánico |
| cuenta `degraded (no engine)` si cargaste cookies después de arrancar | el engine se reconstruye perezosamente en el siguiente beat del job (T-DEPLOY-24); no hace falta reiniciar |
| `database is locked` puntual | contención SQLite normal; `busy_timeout` corrió antes de WAL (T-DB-5); monitorear `db_busy_count_5min` en `daemon status` |
| todo vídeo `failed/integrity` con archivos en disco | ruta de descarga resuelta por glob por id, no por campo fijo: correr una descarga de prueba e inspeccionar qué cambió la nightly (§14.6, T-ENGINE-32/33) |

El resto de los síntomas (TikTok, cookies, bot, Windows) están en `specs/Plan de
implementacion tikdown-rs.md` §14.6 — es la única tabla de verdad, no la duplique
acá.

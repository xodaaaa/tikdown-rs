# TikDown-rs

CLI + Daemon + Telegram para archivar vídeos de TikTok de forma automatizada y resistente a bloqueos.

> **Nota de naming:** el sufijo `-rs` es **histórico**; este proyecto es **Python**, no Rust. Se mantiene por continuidad del nombre.

## Qué es

TikDown-rs es una herramienta de un solo usuario que archiva vídeos de TikTok de forma automatizada y resistente a bloqueos. Se ejecuta en dos modos del mismo código base:

- **Daemon** (`uv run tikdown-rs daemon run`) — proceso de larga duración (entrypoint de Docker) con scheduler, monitor de cuentas, validación de cookies y bot de Telegram.
- **CLI** (`tikdown-rs <grupo> <comando>`) — comandos de un solo disparo (cuentas, backfill, cookies, videos, system).
- **Bot de Telegram** — comandos con paridad funcional con la CLI: `/list`, `/stats`, `/last`, `/disk`, `/cookies`, `/check`, `/add`, `/pause`, `/resume`, `/notify`, `/monitor`, `/backfill`.

> **No implementado (auditoría 3.2):** el **envío push** de notificaciones. Los ciclos que generan eventos (monitor, disco, red, backfill) ya se ejecutan en el daemon (ronda 2), pero el bus `on_event` no está conectado al bot y `NotificationService.send_event()` es un noop — no hay envío real ni spool persistente. Además `telegram_bot_mode` no discrimina comportamiento. Para activarlo: épico propio (ExtBot de notificaciones + spool persistente + cableado del bus).

## Ejecución

```bash
uv sync
uv run tikdown-rs daemon run        # arranca el daemon
uv run tikdown-rs --help            # ayuda de la CLI
```

## Disclaimer legal

> **ADVERTENCIA:** TikDown-rs está pensada para archivar **contenido propio o con permiso explícito**. La responsabilidad sobre los términos de servicio de TikTok y el copyright del contenido descargado recae **íntegramente en el usuario**. No uses esta herramienta para descargar contenido sin autorización. (Estilo yt-dlp.)

## Qué NO commitear

El volumen de datos, cualquier archivo de cookies exportado del navegador, y el `.env` real usado en despliegue **nunca** deben commitearse:

- `data/` (o `DATA_DIR`) — base de datos, vídeos, archivo de deduplicación
- `fernet.key` — clave de cifrado de cookies
- `cookies*.txt` / `cookies*.json` — exportaciones del navegador
- `.env` — variables reales (el `.env.example` es la plantilla)

## Backup y recuperación de `fernet.key`

`fernet.key` (o `FERNET_KEY`) es el **único secreto** que descifra todas las cookies almacenadas (cifrado Fernet en reposo).

1. **Respáldalo** fuera del volumen de datos y fuera del repositorio — en un gestor de secretos o almacenamiento cifrado separado del host.
2. **Si se pierde o corrompe sin respaldo**, todas las cookies en `encrypted_blob` quedan permanentemente irrecuperables. La única salida válida es **purgar la tabla `cookies`** y reimportar cookies frescas — no existe "recuperación" del ciphertext sin la clave.

## Estado del proyecto

MVP completo (v0.3.2): 15 épicos entregados (e01–e15) sobre el mismo código base:

- **Core**: daemon con ciclo único asyncio (L-B1), scheduler (heartbeat, backfill-collect), apagado limpio por señal y por `daemon stop` (bug #21), healthcheck Docker (T50).
- **Accounts**: añadir/pausar/resumir/notificar cuentas, comprobación manual con throttle, backfill con cursor y cola (`queued`) recogida por el daemon (pacing T62), reconciliación de backfills pausados (e13).
- **Telegram**: bot de comandos con paridad funcional de la CLI (§6.4) — `/list /stats /last /disk /cookies /check /add /pause /resume /notify /monitor /backfill` — con doble autorización (§6.3), throttle y polling supervisado con auto-reinicio (e12).
- **Resiliencia**: circuit breaker (clasificación definitiva/transitoria, T52), cooldown global en DB (T62), backoff anti-bot, probe de red con pausa automática (e07), verificación de integridad SHA-256 + ffprobe (e09), métricas y contención DB (e15).
- **Operación**: logs JSON con rotación de archivo (e14), backups con retención y restauración, export del archivo, CI Woodpecker (lint, test con cobertura, build Docker + smoke, e10), cookies cifradas con Fernet (e05).

> **No implementado**: notificaciones push externas — ver nota en la sección Telegram/README (auditoría 3.2).

## Licencia

[MIT](LICENSE) — ver archivo `LICENSE`.

## Backup y restauración de la base de datos

`system backup` crea un snapshot consistente en caliente (`VACUUM INTO`) en `DATA_DIR/backups/`. Se conservan los `SYSTEM_BACKUP_RETAIN_COUNT` (7) más recientes.

**Restaurar (con el daemon DETENIDO):**

1. Detener el daemon (`tikdown-rs daemon stop`).
2. Copiar el snapshot sobre la base: `cp backups/tikdown-rs-<fecha>.db data/tikdown-rs.db`.
3. Eliminar los archivos WAL/SHM asociados: `rm -f data/tikdown-rs.db-wal data/tikdown-rs.db-shm`.
4. Arrancar el daemon de nuevo.

> Nota: el backup restaura cuentas, estado y cookies cifradas; los vídeos ya descargados en disco se reconcilian con el siguiente ciclo.

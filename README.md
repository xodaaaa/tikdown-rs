# TikDown-rs

CLI + Daemon + Telegram para archivar vídeos de TikTok de forma automatizada y resistente a bloqueos.

> **Nota de naming:** el sufijo `-rs` es **histórico**; este proyecto es **Python**, no Rust. Se mantiene por continuidad del nombre.

## Qué es

TikDown-rs es una herramienta de un solo usuario que archiva vídeos de TikTok de forma automatizada y resistente a bloqueos. Se ejecuta en dos modos del mismo código base:

- **Daemon** (`uv run tikdown-rs daemon run`) — proceso de larga duración (entrypoint de Docker) con scheduler, monitor de cuentas, validación de cookies y bot de Telegram.
- **CLI** (`tikdown-rs <grupo> <comando>`) — comandos de un solo disparo (cuentas, backfill, cookies, videos, system).

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

Fundación (epic e01) en curso: toolchain uv, config, logging, modelos + DB, higiene de repo.

## Licencia

[MIT](LICENSE) — ver archivo `LICENSE`.

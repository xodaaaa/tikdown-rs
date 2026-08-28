# Story e01s02 — Configuración Settings (pydantic-settings)

**type:** feat
**risk:** P1
**context:** infra
**BCPs:** 3
**status:** planned

## 1. Business narrative

TikDown-rs necesita una configuración tipada, validada y derivada de variables de entorno (12-factor) para homelab/Docker. `core/config.py` define `Settings` (pydantic-settings) con todas las variables de §12 y `validate_for_daemon()` — fail-fast de configuración al arrancar el daemon (T25).

## 2. Actors

- **Usuario homelab / Docker** — define variables en `.env` o el entorno.
- **Daemon** — llama `validate_for_daemon()` al arrancar (fail-fast).
- **CLI / servicios** — construyen una `Settings` fresca por invocación (§5.6).

## 3. Problem statement

Sin configuración tipada, cada módulo leería variables sueltas del entorno, con errores de tipeo, valores inválidos no detectados y rutas de datos inconsistentes. El plan exige: pydantic-settings, `validate_for_daemon()` fail-fast (T25), y que **cada variable declarada tenga efecto real** (T36).

## 4. Requirements

#### ADDED: Modelo Settings (pydantic-settings) en core/config.py
**After:** `Settings(BaseSettings)` con `model_config = SettingsConfigDict(env_file='.env', extra='ignore')`. Campos con los nombres de §12: `data_dir` (DATA_DIR), `log_level` (LOG_LEVEL), `fernet_key`, `telegram_bot_token`, `telegram_chat_id`, `telegram_user_id`, `telegram_bot_mode`, `enable_external_notifications`, `monitor_interval_minutes`, `monitor_autostart`, `max_concurrent_downloads`, `global_download_cooldown_min_seconds`, `global_download_cooldown_max_seconds`, `ytdlp_antibot_backoff_base_seconds`, `ytdlp_antibot_backoff_ceiling_seconds`, `download_format`, `db_busy_timeout_alert_threshold`, `heartbeat_interval_seconds`, `disk_warning_free_percent`, `system_backup_retain_count`, `max_video_retry_count`, `max_video_total_time_seconds`, `cookie_validation_url`, `ytdlp_proxy_url`, `ytdlp_extractor_args`, `network_probe_url`, `network_probe_interval_seconds`, `network_probe_timeout_seconds`, `network_offline_threshold_consecutive_failures`. Toda ruta de datos deriva de `data_dir` (T8).

#### ADDED: validate_for_daemon() fail-fast
**After:** `validate_for_daemon()` valida la configuración y lanza `ConfigurationError` (o similar) antes de crear recursos si: notificaciones habilitadas sin token, modo `commands`/`both` sin `TELEGRAM_CHAT_ID`, intervalos inválidos (negativos, cero donde no corresponde), o `MAX < MIN` en el cooldown (T25). No se declara variable sin efecto real (T36).

#### ADDED: WEBDAV_* retirado de Settings
**After:** El bloque `WEBDAV_*` NO está en Settings (F-17) — lo lee el sidecar rclone, no la app. No bloquear el arranque por variables que la app no consume.

## 5. Solution and main flow

1. Definir `Settings` con todos los campos de §12 y `model_config` (env_file `.env`, extra ignore).
2. Implementar `validate_for_daemon()` con las reglas de T25.
3. `core/paths.py`: `videos_root()`, `default_outtmpl()` derivan de `data_dir` (T8).
4. Tests: defaults, override por env, validación, derivación de rutas.

## 6. Alternative flows / edge cases

- **`.env` ausente**: defaults aplican; `data_dir` por defecto `/app/data`.
- **Cooldown `MAX < MIN`**: `validate_for_daemon()` falla (T25).
- **Modo `commands`/`both` sin `TELEGRAM_CHAT_ID`**: fail-fast (T25).
- **`FERNET_KEY` vacía**: se genera `fernet.key` en `data_dir` (no es error de config — e05s01).

## 7. Assumptions

- pydantic-settings 2.15.0 instalado (verificado).
- `Settings(_env_file=None, ...)` en tests para aislamiento (§14).

## 8. Constraints

- Toda ruta de datos deriva de `data_dir` (T8); nunca rutas relativas al cwd.
- Cada variable declarada tiene efecto real (T36).
- Sin dependencias externas de caché (Redis no).

## 9. Dependencies

- e01s01 (toolchain, src layout).

## 10. Interfaces

- `core/config.py` → `Settings`, `validate_for_daemon()`.
- `core/paths.py` → `videos_root()`, `default_outtmpl()`.
- Consumido por daemon runner, CLI common, servicios.

## 11. Test plan

- `tests/config/test_settings.py`: defaults, override por env, validación fail-fast, derivación de rutas, `Settings(_env_file=None)`.

## 12. Data

Ninguno (solo configuración; no toca DB).

## 13. Security considerations

- `FERNET_KEY` nunca se commitea (T7); `.env` en `.gitignore`.
- `validate_for_daemon()` evita arranques a medias con config inválida (T25).

## 14. Performance

N/A (config en memoria, TTL simple donde haga falta sin Redis).

## 15. Operational concerns

- `.env.example` documenta variables (e01s05); `Settings` las lee.
- `LOG_LEVEL` controla logging (e01s03).

## 16. Risks

- Variables con nombres desalineados con §12 → validar contra el plan.
- `extra='ignore'` oculta typos en env → mitigar con test de contrato (cada variable declarada conectada).

## 17. Acceptance criteria

- [ ] `core/config.py` define `Settings` con todos los campos de §12.
- [ ] `model_config` usa `env_file='.env'`, `extra='ignore'`.
- [ ] `validate_for_daemon()` falla ante config inválida (T25): sin token con notificaciones, modo commands/both sin chat_id, cooldown `MAX < MIN`, intervalos inválidos.
- [ ] `WEBDAV_*` no está en Settings (F-17).
- [ ] `core/paths.py` deriva rutas de `data_dir` (T8).
- [ ] Tests en `tests/config/test_settings.py` pasan (defaults, override, validación, rutas).

## 18. Out of scope

- `fernet.key` real / cifrado (e05s01).
- Consumo de Settings por daemon/CLI (e02, e08).
- `.env.example` completo (e01s05).

## 19. Risks (detailed)

- **Desalineación de nombres de variables**: mitigado con test de contrato que verifica cada campo mapea a la variable de §12 correcta.

## 20. Definition of done

- Acceptance criteria §17 cumplidos.
- `uv run pytest tests/config/test_settings.py` pasa.
- Tasks `status: passing` en `e01s02-tasks.yaml`.

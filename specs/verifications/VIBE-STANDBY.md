# VIBE — Standby (rate-limit TikTok)

> **Reason for existence:** Estado del entorno de pruebas en pausa. Registro de por qué se detuvo, qué persiste y cómo reanudar.

## Estado

| Campo | Valor |
|-------|-------|
| **Fecha de pausa** | 2026-08-29 |
| **Motivo** | Rate-limit de TikTok sobre la IP residencial (`191.126.187.24`) |
| **Contenedor** | Detenido (`docker compose down`), datos conservados en volumen `tikdown_rs_tikdown_data` |
| **Recuperación estimada** | 24-48h |

## Qué se probó antes de pausar

1. **Cookie nueva** (`cookies-nueva.txt`, #2) — importada y válida, pero el bloqueo es de IP, no de cookie: sin mejora.
2. **Cloudflare WARP+** — IP cambió a `104.28.214.26` (Cloudflare), pero TikTok responde con **WAF challenge (SlardarWAF, página de 1462 bytes)** → el feed ni lista. **Descartado**: IPs de datacenter son bloqueadas con más agresividad que la residencial. WARP desconectado.
3. **Fix bug #18** (PR #3) — `extract_flat` en `extract_profile`: 17→9 peticiones al listar. Merge pendiente.

## Próxima acción (reanudación)

```bash
git checkout vibe && git pull origin vibe
docker compose up -d            # levanta con datos del volumen
docker compose exec tikdown-rs tikdown-rs backfill run rosary657 --queue
# el daemon lo recoge en ≤60s con pacing T62 + listado optimizado (9 peticiones)
docker compose exec tikdown-rs tikdown-rs backfill status rosary657
```

Si el rate-limit persiste tras 48h: probar proxy residencial (no datacenter) vía `ytdlp_proxy_url` en `.env`.

## Verify

```bash
docker volume ls | grep tikdown_data   # el volumen persiste
docker compose ps                       # (sin contenedor — standby)
```

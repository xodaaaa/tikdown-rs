"""Métricas y healthcheck — services/status.py (§3, e15s01).

Lógica de estado en SERVICES (nunca en cli/): cookies (válidas/expirando),
disco (libre, umbral), últimos errores (videos failed), contención SQLite
leída de daemon_state (T19, nunca del proceso CLI).

story: e15s01
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tikdown_rs.core.disk import free_percent
from tikdown_rs.models.models import Cookie, DaemonState, MonitoredAccount, Video

# Cookie "expirando": expiration_date a menos de 7 días
_EXPIRING_DAYS = 7


async def collect_status(session: AsyncSession, settings) -> dict:
    """Reúne métricas para daemon status (§3)."""
    # Cookies: válidas / inválidas / expirando
    cookie_rows = (await session.execute(select(Cookie))).scalars().all()
    valid = sum(1 for c in cookie_rows if c.validation_state == "valid")
    invalid = sum(1 for c in cookie_rows if c.validation_state == "invalid")
    now = datetime.now(UTC)
    expiring = 0
    for c in cookie_rows:
        if c.expiration_date:
            try:
                exp = datetime.fromisoformat(c.expiration_date)
            except ValueError:
                continue
            if exp <= now + timedelta(days=_EXPIRING_DAYS):
                expiring += 1

    # Disco: espacio libre + umbral (T69: free_percent mockeable)
    disk_free = free_percent(settings.data_dir)
    disk_threshold = settings.disk_warning_free_percent
    disk_alert = disk_free < disk_threshold

    # Últimos errores: videos failed, top 5, con cuenta (join)
    recent = (
        await session.execute(
            select(Video, MonitoredAccount.username)
            .join(MonitoredAccount, MonitoredAccount.id == Video.account_id)
            .where(Video.status == "failed")
            .order_by(Video.updated_at.desc())
            .limit(5)
        )
    ).all()
    recent_errors = [
        {
            "timestamp": v.updated_at,
            "category": v.error_category,
            "account": username,
            "message": (v.error_message or "")[:80],
        }
        for v, username in recent
    ]

    # Contención leída de daemon_state (T19), nunca del proceso CLI
    state = (await session.execute(select(DaemonState))).scalar_one_or_none()
    contention = {
        "db_busy_count_5min": state.db_busy_count_5min if state else 0,
    }

    return {
        "cookies": {"valid": valid, "invalid": invalid, "expiring": expiring},
        "disk": {
            "free_percent": disk_free,
            "warning_threshold": disk_threshold,
            "alert": disk_alert,
        },
        "recent_errors": recent_errors,
        "contention": contention,
    }


async def healthcheck_status(session: AsyncSession, settings) -> tuple[bool, list[str]]:
    """Healthcheck ampliado (e15s01): heartbeat fresco (T50) + cookie válida +
    disco con espacio + sin errores críticos recientes.

    LIGERO (§22.1): sin validaciones de red ni selfcheck pesado — corre cada
    ~30s en el HEALTHCHECK de Docker. Devuelve (ok, razones_de_fallo).
    """
    reasons: list[str] = []

    # 1. Heartbeat fresco (T50): last_heartbeat_at <= 3x intervalo
    state = (await session.execute(select(DaemonState))).scalar_one_or_none()
    if state is None or not state.last_heartbeat_at:
        reasons.append("heartbeat: no state")
    else:
        try:
            ts = datetime.fromisoformat(state.last_heartbeat_at)
            age = (datetime.now(UTC) - ts).total_seconds()
            if age > 3 * settings.heartbeat_interval_seconds:
                reasons.append("heartbeat: stale")
        except ValueError:
            reasons.append("heartbeat: invalid timestamp")

    # 2. Al menos una cookie válida
    valid_count = (
        await session.execute(
            select(func.count()).select_from(Cookie).where(Cookie.validation_state == "valid")
        )
    ).scalar_one()
    if valid_count < 1:
        reasons.append("cookies: no valid cookies")

    # 3. Disco con espacio suficiente
    disk_free = free_percent(settings.data_dir)
    if disk_free < settings.disk_warning_free_percent:
        reasons.append(f"disk: {disk_free:.1f}% below threshold")

    # 4. Sin errores críticos recientes (videos failed definitive < 24h)
    cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    critical_recent = (
        await session.execute(
            select(func.count())
            .select_from(Video)
            .where(
                Video.status == "failed",
                Video.error_category == "definitive",
                Video.updated_at >= cutoff,
            )
        )
    ).scalar_one()
    if critical_recent >= 3:  # umbral: 3 errores definitivos en 24h
        reasons.append(f"errors: {critical_recent} critical in 24h")

    return (not reasons), reasons

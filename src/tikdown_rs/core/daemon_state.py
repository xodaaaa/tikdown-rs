"""Acceso al singleton daemon_state (T17, L-C6, T37).

story: e01s04
"""

from __future__ import annotations

from datetime import UTC

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tikdown_rs.models.models import DaemonState


async def get_or_create_daemon_state(session: AsyncSession) -> DaemonState:
    """Relee el singleton; si no existe, lo crea con INSERT ... ON CONFLICT DO NOTHING (T17).

    El INSERT ... ON CONFLICT DO NOTHING se commitea de inmediato (L-C6): sin commit,
    la sesión hace rollback al salir y la fila nunca persiste.
    """
    result = await session.execute(select(DaemonState).where(DaemonState.id == 1))
    row = result.scalar_one_or_none()
    if row is not None:
        return row

    # Idempotente bajo concurrencia: dos procesos pueden llegar aquí a la vez.
    session.add(DaemonState(id=1))
    try:
        await session.commit()
    except Exception:
        await session.rollback()
    # Releer (el otro proceso pudo ganar la carrera).
    result = await session.execute(select(DaemonState).where(DaemonState.id == 1))
    row = result.scalar_one()
    return row


async def _mutate(session: AsyncSession, **values) -> None:
    """Actualiza el singleton y COMMITEA internamente (T37).

    Los helpers mutadores no dependen del commit del llamador: una sesión corta
    hace rollback silencioso al salir si el commit se olvida (T37).
    """
    await get_or_create_daemon_state(session)
    await session.execute(update(DaemonState).where(DaemonState.id == 1).values(**values))
    await session.commit()


async def set_stop_requested(session: AsyncSession, value: bool) -> None:
    """Escribe stop_requested (T37: commit interno).

    Lo usa `daemon stop` vía SQLite; el watcher del daemon lo detecta.
    """
    await _mutate(session, stop_requested=value)


async def set_monitor_running(session: AsyncSession, value: bool) -> None:
    """Escribe monitor_running (T37: commit interno).

    Lo usa `monitor start/stop`; el heartbeat del daemon lo aplica en caliente.
    """
    await _mutate(session, monitor_running=value)


async def update_heartbeat(session: AsyncSession, pid: int | None = None) -> None:
    """Actualiza el heartbeat (T37: commit interno)."""
    from datetime import datetime

    values: dict = {
        "last_heartbeat_at": datetime.now(UTC).isoformat(),
    }
    if pid is not None:
        values["daemon_pid"] = pid
    await _mutate(session, **values)

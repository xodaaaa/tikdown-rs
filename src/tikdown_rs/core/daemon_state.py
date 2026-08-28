"""Acceso al singleton daemon_state (T17, L-C6, T37).

story: e01s04
"""

from __future__ import annotations

from sqlalchemy import select
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

"""Gestión de disco — core/disk.py (§4.3 punto 6, T45/T65).

ENOSPC → downloads_paused=1 (fallo local accionable, no cuenta para breaker
ni toca cookies). Job de disco productor de monitor.disk_warning + reanudación
automática al recuperar espacio (T65). shutil.disk_usage SIEMPRE mockeado en
tests (T69).

story: e07s02
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from tikdown_rs.core.config import Settings
from tikdown_rs.core.daemon_state import get_or_create_daemon_state
from tikdown_rs.models.models import DaemonState

LOG = logging.getLogger("tikdown_rs.disk")


async def set_downloads_paused(session: AsyncSession, paused: bool) -> None:
    """Escribe downloads_paused con commit interno (T37)."""
    await get_or_create_daemon_state(session)
    await session.execute(
        update(DaemonState).where(DaemonState.id == 1).values(downloads_paused=paused)
    )
    await session.commit()


def free_percent(data_dir: Path) -> float:
    """% de espacio libre en data_dir (shutil.disk_usage; mockeado en tests, T69)."""
    usage = shutil.disk_usage(str(data_dir))
    # indexado: compatible con namedtuple y mocks de tupla (T69)
    return usage[2] / usage[0] * 100.0


async def check_disk_usage(
    session: AsyncSession,
    settings: Settings,
    on_event=None,
) -> bool:
    """Comprueba el espacio libre; pausa/reanuda según umbral (T45/T65).

    Returns: True si downloads quedaron pausadas.
    """
    percent = free_percent(settings.data_dir)
    threshold = settings.disk_warning_free_percent
    if percent < threshold:
        await set_downloads_paused(session, True)  # T45: fallo local accionable
        if on_event:
            on_event("monitor.disk_warning", {"free_percent": round(percent, 1)})
        LOG.warning("disk.warning", extra={"free_percent": round(percent, 1)})
        return True
    # Espacio libre de nuevo → reanudación automática (T65)
    if percent >= threshold:
        await set_downloads_paused(session, False)
    return False


async def disk_job(
    session: AsyncSession,
    settings: Settings,
    on_event=None,
) -> bool:
    """Job de disco (15-30 min): productor de monitor.disk_warning (T65)."""
    return await check_disk_usage(session, settings, on_event=on_event)

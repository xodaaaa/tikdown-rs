"""Ciclo del monitor — services/monitor.py (§4.9).

Descubre vídeos nuevos de cuentas en mode=monitor, respetando el throttle de
30s por cuenta (L-G1: NULL siempre se comprueba; <30s se salta). El monitor
arranca siempre detenido (§5.1/T60) y NO arranca backfills (§10).

story: e03s02
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tikdown_rs.models.models import MonitoredAccount

LOG = logging.getLogger("tikdown_rs.monitor")

THROTTLE_SECONDS = 30  # §4.9


def _should_check(account: MonitoredAccount, now: datetime) -> bool:
    """¿Se comprueba esta cuenta ahora? (throttle L-G1).

    - last_check_at NULL (nunca comprobada) → SIEMPRE se comprueba.
    - last_check_at < 30s (recién comprobada) → se salta.
    - last_check_at >= 30s → se comprueba.

    Tratar NULL como 0 segundos era el bug (L-G1): cuentas recién añadidas
    nunca se comprobaban.
    """
    if account.last_check_at is None:
        return True  # L-G1: nunca comprobada → siempre
    ts = datetime.fromisoformat(account.last_check_at)
    age = (now - ts).total_seconds()
    return age >= THROTTLE_SECONDS


async def run_monitor_cycle(
    session: AsyncSession,
    discover_fn: Callable[[AsyncSession, MonitoredAccount], Awaitable[None]],
    throttle_seconds: int = THROTTLE_SECONDS,
) -> list[str]:
    """Ejecuta un ciclo del monitor (§4.9).

    Procesa cuentas mode=monitor y no pausadas, respetando el throttle.
    `discover_fn` (inyectada) descubre vídeos nuevos y encola descargas al
    motor — el monitor NO arranca backfills (§10).
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(MonitoredAccount).where(MonitoredAccount.mode == "monitor")
    )
    accounts_list = list(result.scalars().all())
    processed: list[str] = []

    for account in accounts_list:
        if account.paused:
            continue  # pausada → skip
        if account.last_check_at is not None:
            ts = datetime.fromisoformat(account.last_check_at)
            age = (now - ts).total_seconds()
            if age < throttle_seconds:
                continue  # throttle (recién comprobada, L-G1)
        # L-G1: last_check_at NULL → se comprueba siempre (sin skip)

        await discover_fn(session, account)
        account.last_check_at = now.isoformat()
        processed.append(account.username)

    if processed:
        await session.commit()
        LOG.info("monitor.cycle_done", extra={"processed": len(processed)})
    return processed

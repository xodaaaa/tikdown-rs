"""Circuit breaker por cuenta — core/breaker.py (§4.4).

5 fallos de AUTH consecutivos → paused + needs_review. Los transitorios
(T5/T52), red (T64) y disco (T45) NO cuentan. El contador vive en memoria
del proceso (se resetea al reiniciar); las pausas persisten en DB.
Emite monitor.account_paused (F-08).

story: e07s03
"""

from __future__ import annotations

import logging

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from tikdown_rs.models.models import MonitoredAccount

LOG = logging.getLogger("tikdown_rs.breaker")

# Categorías que CUENTAN para el breaker: solo auth (§4.4)
_TRIP_CATEGORIES = {"auth"}


class AccountBreaker:
    """Breaker por cuenta con contador en memoria (§4.4)."""

    def __init__(self, threshold: int = 5) -> None:
        self.threshold = threshold
        self._counts: dict[str, int] = {}  # en memoria (reset al reiniciar)

    def count_for(self, username: str) -> int:
        return self._counts.get(username, 0)

    async def record_result(
        self,
        session: AsyncSession,
        username: str,
        category: str,
        on_event=None,
    ) -> bool:
        """Registra el resultado de una operación por cuenta.

        - 'auth' → incrementa (T52: solo auth real cuenta).
        - 'success' → resetea el contador.
        - 'transient'/'network'/'disk'/'integrity' → NO cuentan (T5/T45/T64).

        Returns: True si el breaker disparó (cuenta pausada).
        """
        if category == "success":
            self._counts.pop(username, None)
            return False
        if category not in _TRIP_CATEGORIES:
            return False  # transitorio/red/disco no cuentan (T5/T45/T64)

        count = self._counts.get(username, 0) + 1
        self._counts[username] = count
        if count < self.threshold:
            return False

        # Disparo: pausar cuenta + needs_review (persiste en DB, §4.4)
        self._counts.pop(username, None)
        await session.execute(
            update(MonitoredAccount)
            .where(MonitoredAccount.username == username)
            .values(paused=True, needs_review=True)
        )
        await session.commit()
        LOG.warning("breaker.tripped", extra={"username": username, "count": count})
        if on_event:
            on_event("monitor.account_paused", {"username": username})  # F-08
        return True

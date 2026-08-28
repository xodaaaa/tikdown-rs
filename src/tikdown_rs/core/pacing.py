"""Cooldown global cross-proceso — core/pacing.py (§4.5).

Espacia descargas entre TODOS los procesos (daemon + CLI) vía SQLite
(download_pacing_state). reserve() es atómico con UPDATE ... RETURNING (T22);
sorteo uniforme [MIN, MAX] con RNG inyectable (T62); timestamps con
milisegundos (L-C7); singleton con commit inmediato (L-C6).

story: e04s01
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class CooldownReserve:
    """Parámetros del sorteo de cooldown (T62)."""

    min_seconds: int = 30
    max_seconds: int = 120
    rng: random.Random | None = None

    def draw(self) -> float:
        """Sorteo uniforme en [MIN, MAX]; MIN=MAX fijo; ambos 0 desactivado (T62)."""
        if self.min_seconds == 0 and self.max_seconds == 0:
            return 0.0
        rng = self.rng or random
        return rng.uniform(self.min_seconds, self.max_seconds)


async def reserve_slot(session: AsyncSession, reserve: CooldownReserve) -> float:
    """Reserva un hueco de cooldown de forma ATÓMICA y cross-proceso (T22).

    Sorteo + marcar el siguiente hueco ocurren en UNA operación
    (UPDATE ... RETURNING, SQLite >= 3.35). Devuelve el delay sorteado.
    """
    delay = reserve.draw()

    # Singleton con INSERT ... ON CONFLICT DO NOTHING + commit inmediato (L-C6).
    # session.add NO hace ON CONFLICT (bug #16): con la fila existente lanza
    # IntegrityError (pk duplicado) y rompe el pacing. SQL nativo lo resuelve.
    await session.execute(
        text(
            "INSERT INTO download_pacing_state (id, next_allowed_at) "
            "VALUES (1, NULL) ON CONFLICT(id) DO NOTHING"
        )
    )
    await session.commit()

    next_allowed = datetime.now(UTC) + timedelta(seconds=delay)
    # L-C7: precisión de milisegundos (sigue siendo lexicográficamente comparable)
    next_allowed_str = next_allowed.isoformat(timespec="milliseconds")

    # UPDATE ... RETURNING: el efecto (marcar el hueco) es lo que importa (T22)
    await session.execute(
        text(
            "UPDATE download_pacing_state "
            "SET next_allowed_at = :next "
            "WHERE id = 1 "
            "RETURNING next_allowed_at"
        ),
        {"next": next_allowed_str},
    )
    await session.commit()
    return delay

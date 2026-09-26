"""download_pacing_state singleton model and atomic reservation helper. Regla: 3.5.

Trampas neutralizadas: T-DB-6, T-DB-7, T-DB-12, T-ENGINE-26. next_allowed_at is
ISO8601 UTC with milliseconds timespec; reservation is a native CAS UPDATE ...
RETURNING after an idempotent native bootstrap INSERT, with IMMEDIATE commit.
"""

from sqlalchemy import CheckConstraint, Integer, Text, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tikdown_rs.models import Base


class DownloadPacingState(Base):
    """Singleton row: exactly one, enforced by CHECK (id = 1)."""

    __tablename__ = "download_pacing_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_download_pacing_state_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    next_allowed_at: Mapped[str | None] = mapped_column(Text, nullable=True)


async def read_next_allowed_at(session: AsyncSession) -> str | None:
    """Read the singleton's current slot; None when the row is absent."""
    row = await session.get(DownloadPacingState, 1)
    return row.next_allowed_at if row is not None else None


async def reserve_download_slot(session: AsyncSession, now: str, next_allowed: str) -> bool:
    """Atomically claim the next download slot.

    Wins only if the stored next_allowed_at is NULL or already due (<= now);
    the winner stores next_allowed. Returns True when this call won. Native
    INSERT ... ON CONFLICT DO NOTHING bootstrap (T-DB-12) + CAS UPDATE ...
    RETURNING; IMMEDIATE commit (T-DB-6, T-DB-13).
    """
    bootstrap = (
        sqlite_insert(DownloadPacingState)
        .values(id=1)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    await session.execute(bootstrap)
    result = await session.execute(
        text(
            "UPDATE download_pacing_state SET next_allowed_at = :next_allowed"
            " WHERE id = 1 AND (next_allowed_at IS NULL OR next_allowed_at <= :now)"
            " RETURNING next_allowed_at"
        ),
        {"next_allowed": next_allowed, "now": now},
    )
    won = result.scalar() is not None
    await session.commit()
    return won

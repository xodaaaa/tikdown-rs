"""backfill_slot singleton model and CAS helpers. Regla: 3.5.

Trampas neutralizadas: T-BACKFILL-20, T-DB-6, T-DB-12, T-DB-13. The CAS UPDATE
... RETURNING makes the slot visible to daemon, CLI and bot across processes,
instead of a single-process asyncio.Lock.
"""

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, Integer, Text, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tikdown_rs.models import Base


class BackfillSlot(Base):
    """Singleton row: exactly one, enforced by CHECK (id = 1)."""

    __tablename__ = "backfill_slot"
    __table_args__ = (CheckConstraint("id = 1", name="ck_backfill_slot_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    acquired_at: Mapped[str | None] = mapped_column(Text, nullable=True)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


async def acquire_backfill_slot(session: AsyncSession, owner: str) -> str | None:
    """Try to take the slot; return the winning owner or None if already held.

    Idempotent row bootstrap with native INSERT ... ON CONFLICT DO NOTHING
    (T-DB-12), then a single CAS UPDATE ... RETURNING; IMMEDIATE commit
    (T-DB-6, T-DB-13).
    """
    bootstrap = (
        sqlite_insert(BackfillSlot).values(id=1).on_conflict_do_nothing(index_elements=["id"])
    )
    await session.execute(bootstrap)
    result = await session.execute(
        text(
            "UPDATE backfill_slot SET owner = :owner, acquired_at = :now"
            " WHERE id = 1 AND owner IS NULL RETURNING owner"
        ),
        {"owner": owner, "now": _utc_now()},
    )
    won = result.scalar()
    await session.commit()
    return won


async def release_backfill_slot(session: AsyncSession, owner: str) -> None:
    """Release the slot only if the caller owns it (WHERE owner = :me)."""
    await session.execute(
        text(
            "UPDATE backfill_slot SET owner = NULL, acquired_at = NULL"
            " WHERE id = 1 AND owner = :owner"
        ),
        {"owner": owner},
    )
    await session.commit()

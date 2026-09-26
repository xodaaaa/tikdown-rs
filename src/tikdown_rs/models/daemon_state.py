"""daemon_state singleton table model and row bootstrap helper.

Trampas neutralizadas: T-DB-6, T-DB-11, T-DB-12, T-DB-13. Regla: §3.5, §3.7.
"""

from sqlalchemy import Boolean, CheckConstraint, Integer, Text, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tikdown_rs.models import Base


class DaemonState(Base):
    """Singleton coordination row: exactly one row, enforced by CHECK (id = 1)."""

    __tablename__ = "daemon_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_daemon_state_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    monitor_running: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    stop_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    daemon_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daemon_started_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_heartbeat_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    db_busy_count_5min: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    downloads_paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0")
    )
    last_known_good_ytdlp_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_selfcheck_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_selfcheck_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


async def ensure_daemon_state_row(session: AsyncSession) -> DaemonState:
    """Insert the singleton row if absent, commit immediately, then re-read it.

    Native INSERT ... ON CONFLICT DO NOTHING (T-DB-11, T-DB-12: never session.add()
    on the PK-occupied singleton); IMMEDIATE commit so the cooldown bootstrap never
    fails open (T-DB-6, T-DB-13).
    """
    statement = (
        sqlite_insert(DaemonState).values(id=1).on_conflict_do_nothing(index_elements=["id"])
    )
    await session.execute(statement)
    await session.commit()
    row = await session.get(DaemonState, 1)
    if row is None:
        raise RuntimeError("daemon_state singleton row missing after ON CONFLICT DO NOTHING insert")
    return row

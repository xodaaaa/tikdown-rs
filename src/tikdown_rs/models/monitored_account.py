"""monitored_accounts model. Regla: 3.1.

Trampas neutralizadas: T-BACKFILL-9, T-DATA-10. Every enum state lives in the
CHECK from the first schema: adding a CHECK value later is a table migration.
"""

from sqlalchemy import Boolean, CheckConstraint, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from tikdown_rs.models import Base

BACKFILL_STATUSES = (
    "idle",
    "queued",
    "backfilling",
    "paused",
    "completed",
    "failed",
    "cancelled",
)


class MonitoredAccount(Base):
    """A TikTok account the daemon monitors or backfills."""

    __tablename__ = "monitored_accounts"
    __table_args__ = (
        CheckConstraint("mode IN ('history', 'monitor')", name="ck_monitored_accounts_mode"),
        CheckConstraint(
            "backfill_status IN ('idle', 'queued', 'backfilling', 'paused',"
            " 'completed', 'failed', 'cancelled')",
            name="ck_monitored_accounts_backfill_status",
        ),
        CheckConstraint(
            "backfill_pause_reason IS NULL OR backfill_pause_reason IN ('disk', 'network')",
            name="ck_monitored_accounts_backfill_pause_reason",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="history")
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    notify_on_download: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    monitor_after_backfill: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="0"
    )
    backfill_status: Mapped[str] = mapped_column(Text, nullable=False)
    backfill_pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    backfill_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    backfill_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    backfill_done: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_check_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    follower_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    following_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_last_refreshed: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Incremented by handle_download_result in the same transaction as each
    # successful download (T-DATA-10); never recomputed from the filesystem.
    total_disk_bytes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str | None] = mapped_column(Text, nullable=True)

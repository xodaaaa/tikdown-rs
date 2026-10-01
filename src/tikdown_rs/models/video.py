"""videos model. Reglas: 3.2, 3.3.

Trampa neutralizada: T-DATA-1. `pending` is in the status CHECK from the first
schema: without it the monitor's discovered-video INSERT fails silently, swallowed
as a WARNING, indistinguishable from "the account posted nothing".
"""

from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from tikdown_rs.models import Base


class VideoStatus(StrEnum):
    """Video status; values are byte-identical to the CHECK literals (3.3)."""

    PENDING = "pending"
    DOWNLOADED = "downloaded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ErrorCategory(StrEnum):
    """Error-category domain (4.4).

    DEFINITIVE/TRANSIENT/INTEGRITY are the stored CHECK values. LOCAL
    (disk-full, T-ENGINE-27) and INFO (empty account) are classifier outputs
    the CHECK cannot store: the row persists with error_category NULL and the
    category stays actionable in logs (4.7).
    """

    DEFINITIVE = "definitive"
    TRANSIENT = "transient"
    LOCAL = "local"
    INFO = "info"
    INTEGRITY = "integrity"


class Video(Base):
    """A discovered/downloaded TikTok video."""

    __tablename__ = "videos"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'downloaded', 'failed', 'cancelled', 'skipped')",
            name="ck_videos_status",
        ),
        CheckConstraint(
            "error_category IS NULL OR error_category IN ('definitive', 'transient', 'integrity')",
            name="ck_videos_error_category",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tiktok_video_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("monitored_accounts.id"), nullable=True
    )
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Canonical YYYYMMDD upload date, never mixed with ISO8601 (T-ENGINE-25).
    upload_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    downloaded_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str | None] = mapped_column(Text, nullable=True)

"""Modelos SQLAlchemy async de TikDown-rs (§2 del plan maestro).

story: e01s04
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    """ISO8601 UTC actual (para defaults de timestamps)."""
    return _utcnow().isoformat()


class Base(DeclarativeBase):
    pass


class MonitoredAccount(Base):
    __tablename__ = "monitored_accounts"
    __table_args__ = (
        CheckConstraint("mode IN ('history', 'monitor')", name="ck_accounts_mode"),
        CheckConstraint(
            "backfill_status IN ('idle','queued','backfilling','paused',"
            "'completed','failed','cancelled')",
            name="ck_accounts_backfill_status",
        ),
        Index("ix_accounts_username", "username"),
        Index("ix_accounts_backfill_status", "backfill_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="history")
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_on_download: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    monitor_after_backfill: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    backfill_status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    backfill_pause_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    backfill_cursor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    backfill_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    backfill_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_check_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    follower_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    following_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_last_refreshed: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=_now_iso)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=_now_iso)


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        CheckConstraint(
            "status IN ('downloaded','failed','cancelled','skipped')",
            name="ck_videos_status",
        ),
        CheckConstraint(
            "error_category IN ('definitive','transient','integrity')",
            name="ck_videos_error_category",
        ),
        Index("ix_videos_tiktok_video_id", "tiktok_video_id"),
        Index("ix_videos_account_downloaded", "account_id", "downloaded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tiktok_video_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitored_accounts.id"), nullable=True
    )
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upload_date: Mapped[str | None] = mapped_column(String(8), nullable=True)  # YYYYMMDD (T43)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # absoluto, DATA_DIR (T8)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # SHA-256
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="downloaded")
    downloaded_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=_now_iso)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=_now_iso)


class Cookie(Base):
    __tablename__ = "cookies"
    __table_args__ = (
        CheckConstraint(
            "validation_state IN ('valid','invalid','inconclusive')",
            name="ck_cookies_validation_state",
        ),
        Index("ix_cookies_validation_state", "validation_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expiration_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_validated_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    validation_state: Mapped[str] = mapped_column(String(16), nullable=False, default="valid")
    last_validation_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=_now_iso)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=_now_iso)


class DaemonState(Base):
    __tablename__ = "daemon_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_daemon_state_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    monitor_running: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stop_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    daemon_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daemon_started_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_heartbeat_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    db_busy_count_5min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    downloads_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_known_good_ytdlp_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_notified_ytdlp_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_selfcheck_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_selfcheck_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class DownloadPacingState(Base):
    __tablename__ = "download_pacing_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_download_pacing_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    next_allowed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class BackfillSlot(Base):
    """Slot único de backfill CROSS-PROCESO (e13s01, T22).

    Singleton id=1 (mismo patrón que download_pacing_state): adquisición
    atómica con UPDATE ... SET owner=:me WHERE owner IS NULL RETURNING
    (CAS vía SQLite), visible para daemon + CLI + bot.
    """

    __tablename__ = "backfill_slot"
    __table_args__ = (CheckConstraint("id = 1", name="ck_backfill_slot_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acquired_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class DownloadArchive(Base):
    __tablename__ = "download_archive"
    __table_args__ = (UniqueConstraint("tiktok_video_id", name="uq_download_archive_video"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tiktok_video_id: Mapped[str] = mapped_column(String(64), nullable=False)


class PendingNotification(Base):
    __tablename__ = "pending_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=_now_iso)

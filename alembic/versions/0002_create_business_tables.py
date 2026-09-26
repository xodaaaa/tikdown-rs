"""create business tables

Creates monitored_accounts, videos, cookies, backfill_slot,
download_pacing_state and download_archive. PURE DDL: it inserts NO rows.
Singleton rows are bootstrapped at runtime by their idempotent helpers
(3.5); every enum state lives in its CHECK from the first schema
(T-BACKFILL-9, T-DATA-1).

Revision ID: 55a967f19162
Revises: 0001_daemon_state
Create Date: 2026-09-26 17:59:49.046905

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "55a967f19162"
down_revision: str | None = "0001_daemon_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitored_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), server_default="history", nullable=False),
        sa.Column("paused", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("needs_review", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("notify_on_download", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("monitor_after_backfill", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("backfill_status", sa.Text(), nullable=False),
        sa.Column("backfill_pause_reason", sa.Text(), nullable=True),
        sa.Column("backfill_cursor", sa.Text(), nullable=True),
        sa.Column("backfill_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("backfill_done", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_check_at", sa.Text(), nullable=True),
        sa.Column("follower_count", sa.Integer(), nullable=True),
        sa.Column("following_count", sa.Integer(), nullable=True),
        sa.Column("total_likes", sa.Integer(), nullable=True),
        sa.Column("video_count", sa.Integer(), nullable=True),
        sa.Column("profile_last_refreshed", sa.Text(), nullable=True),
        sa.Column("total_disk_bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
        sa.CheckConstraint(
            "mode IN ('history', 'monitor')", name="ck_monitored_accounts_mode"
        ),
        sa.CheckConstraint(
            "backfill_status IN ('idle', 'queued', 'backfilling', 'paused',"
            " 'completed', 'failed', 'cancelled')",
            name="ck_monitored_accounts_backfill_status",
        ),
        sa.CheckConstraint(
            "backfill_pause_reason IS NULL OR"
            " backfill_pause_reason IN ('disk', 'network')",
            name="ck_monitored_accounts_backfill_pause_reason",
        ),
    )
    op.create_table(
        "videos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tiktok_video_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("upload_date", sa.Text(), nullable=True),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("file_hash", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("downloaded_at", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_category", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["monitored_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tiktok_video_id"),
        sa.CheckConstraint(
            "status IN ('pending', 'downloaded', 'failed', 'cancelled', 'skipped')",
            name="ck_videos_status",
        ),
        sa.CheckConstraint(
            "error_category IS NULL OR"
            " error_category IN ('definitive', 'transient', 'integrity')",
            name="ck_videos_error_category",
        ),
    )
    op.create_table(
        "cookies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("cookie_blob", sa.LargeBinary(), nullable=False),
        sa.Column("expiration_date", sa.Text(), nullable=True),
        sa.Column("last_validated_at", sa.Text(), nullable=True),
        sa.Column("validation_state", sa.Text(), nullable=False),
        sa.Column("last_validation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "validation_state IN ('valid', 'invalid', 'inconclusive')",
            name="ck_cookies_validation_state",
        ),
    )
    op.create_table(
        "backfill_slot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=True),
        sa.Column("acquired_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_backfill_slot_singleton"),
    )
    op.create_table(
        "download_pacing_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("next_allowed_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_download_pacing_state_singleton"),
    )
    op.create_table(
        "download_archive",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tiktok_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["monitored_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("download_archive")
    op.drop_table("download_pacing_state")
    op.drop_table("backfill_slot")
    op.drop_table("cookies")
    op.drop_table("videos")
    op.drop_table("monitored_accounts")

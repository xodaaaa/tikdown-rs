"""Create the daemon_state singleton table (the project marker table).

The migration creates the table ONLY; it inserts NO rows. The singleton row is
bootstrapped at runtime by tikdown_rs.models.daemon_state.ensure_daemon_state_row
(T-DB-6, T-DB-11, T-DB-12).

Revision ID: 0001_daemon_state
Revises:
Create Date: 2026-01-01

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_daemon_state"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daemon_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("monitor_running", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("stop_requested", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("daemon_pid", sa.Integer(), nullable=True),
        sa.Column("daemon_started_at", sa.Text(), nullable=True),
        sa.Column("last_heartbeat_at", sa.Text(), nullable=True),
        sa.Column("db_busy_count_5min", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("downloads_paused", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_known_good_ytdlp_version", sa.Text(), nullable=True),
        sa.Column("last_selfcheck_at", sa.Text(), nullable=True),
        sa.Column("last_selfcheck_ok", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_daemon_state_singleton"),
    )


def downgrade() -> None:
    op.drop_table("daemon_state")

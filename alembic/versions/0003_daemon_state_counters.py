"""Add daemon-process counter columns to daemon_state (M9).

ytdlp_zombie_threads and supervised_tasks are persisted by the heartbeat job
so out-of-process `daemon status` reports real values instead of
'n/a (in-process)' (T-ASYNC-14, T-DATA-2). NULLABLE: NULL means no daemon
heartbeat has reported the counters yet (a row written before this
capability existed); the daemon writes concrete values on every beat.
PURE DDL: it inserts NO rows. Idempotent: a column is added only when
missing (safe on hand-built legacy databases).

Revision ID: 0003_daemon_state_counters
Revises: 55a967f19162
Create Date: 2026-09-27

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_daemon_state_counters"
down_revision: str | None = "55a967f19162"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in [c["name"] for c in inspector.get_columns(table)]


def upgrade() -> None:
    if not _has_column("daemon_state", "supervised_tasks"):
        op.add_column("daemon_state", sa.Column("supervised_tasks", sa.Integer(), nullable=True))
    if not _has_column("daemon_state", "ytdlp_zombie_threads"):
        op.add_column(
            "daemon_state", sa.Column("ytdlp_zombie_threads", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("daemon_state", "ytdlp_zombie_threads")
    op.drop_column("daemon_state", "supervised_tasks")

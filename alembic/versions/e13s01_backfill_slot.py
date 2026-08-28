"""e13s01 — backfill_slot (cross-proceso) + backfill_pause_reason

Revision ID: e13s01
Revises: 40de73542d9d
Create Date: 2026-08-28

story: e13s01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e13s01_backfill_slot"
down_revision = "40de73542d9d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # backfill_slot: singleton cross-proceso (T22)
    op.create_table(
        "backfill_slot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=True),
        sa.Column("acquired_at", sa.String(length=32), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_backfill_slot_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    # pause_reason en monitored_accounts
    op.add_column(
        "monitored_accounts",
        sa.Column("backfill_pause_reason", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("monitored_accounts", "backfill_pause_reason")
    op.drop_table("backfill_slot")

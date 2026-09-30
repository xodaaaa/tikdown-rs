"""Drop the download_archive mirror table (plan §3.6 amendment).

The text file <DATA_DIR>/download_archive.txt is the single source of truth;
the queryable mirror table is removed. PURE DDL: it deletes NO rows that
anyone reads. Downgrade recreates the table EXACTLY as 0002 did.

Revision ID: 0004_drop_download_archive
Revises: 0003_daemon_state_counters
Create Date: 2026-09-27

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_drop_download_archive"
down_revision: str | None = "0003_daemon_state_counters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("download_archive")


def downgrade() -> None:
    op.create_table(
        "download_archive",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tiktok_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["monitored_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

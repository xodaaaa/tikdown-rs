"""Preserve the download_archive mirror as an inert renamed table.

The text file <DATA_DIR>/download_archive.txt is the single source of truth;
the queryable mirror table is retired from the schema's LIVE namespace.

R1-ARCHIVE-DESTRUCTIVE-MIGRATION (review of lineage review-fbf3555fc8210fff):
a plain ``drop_table`` IRREVERSIBLY destroys every row, including the
account attribution and created-at metadata the .txt file does not retain,
and the downgrade could not reconstruct them. So the table is RENAMED to
``download_archive_removed_0004`` instead: no new code reads or writes it,
every surviving row keeps its reference day, and the downgrade is a plain
reverse rename. Dropping the renamed table for good is a per-database
decision of the operator (ALTER TABLE), never this migration's default.

Revision ID: 0004_drop_download_archive
Revises: 0003_daemon_state_counters
Create Date: 2026-09-27

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_drop_download_archive"
down_revision: str | None = "0003_daemon_state_counters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REMOVED_TABLE = "download_archive_removed_0004"


def upgrade() -> None:
    # RENAME keeps every column, FK and index; SQLite requires no index
    # bookkeeping for a table rename (they follow the table).
    op.rename_table("download_archive", _REMOVED_TABLE)


def downgrade() -> None:
    op.rename_table(_REMOVED_TABLE, "download_archive")

"""video pending status — CHECK ck_videos_status admite 'pending' (2.1-bis, ronda 3).

'descubierto por el monitor, aún no descargado'. Regenerada con
`alembic revision` (ronda 4, hallazgo 3.1) para trazabilidad real del
historial de revisiones.

story: e03s02
"""

from __future__ import annotations

import sqlalchemy as sa  # noqa: F401  (convención de plantilla Alembic)
from alembic import op

revision: str = "c09bde76270e"
down_revision: str | None = "e13s01_backfill_slot"
branch_labels = None
depends_on = None

_OLD = "ck_videos_status"
_OLD_SQL = "status IN ('downloaded','failed','cancelled','skipped')"
_NEW_SQL = "status IN ('downloaded','failed','cancelled','skipped','pending')"


def upgrade() -> None:
    # SQLite: recreate-collective vía batch_alter_table (copy-por-tabla, T68)
    with op.batch_alter_table("videos", schema=None) as batch_op:
        batch_op.drop_constraint(_OLD, type_="check")
        batch_op.create_check_constraint(_OLD, _NEW_SQL)


def downgrade() -> None:
    # Reversa solo posible si no quedan filas 'pending' (se marcan failed)
    op.execute(
        "UPDATE videos SET status='failed', error_message='downgrade' WHERE status='pending'"
    )
    with op.batch_alter_table("videos", schema=None) as batch_op:
        batch_op.drop_constraint(_OLD, type_="check")
        batch_op.create_check_constraint(_OLD, _OLD_SQL)

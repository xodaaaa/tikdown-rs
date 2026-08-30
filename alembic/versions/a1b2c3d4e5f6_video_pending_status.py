"""e13s01-r3 — Video.status admite 'pending' (descubierto, aún no descargado).

Bug 2.1-bis (ronda 3): daemon_discover() insertaba status='new' que violaba
el CHECK ck_videos_status → el commit fallaba silenciosamente justo cuando
el monitor encontraba contenido nuevo.

story: e03s02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6_pending_status"
down_revision = "e13s01_backfill_slot"
branch_labels = None
depends_on = None

_OLD = "ck_videos_status"
_OLD_SQL = "status IN ('downloaded','failed','cancelled','skipped')"
_NEW_SQL = (
    "status IN ('downloaded','failed','cancelled','skipped','pending')"
)


def upgrade() -> None:
    # SQLite: recreate-collective vía batch_alter_table (copy-por-tabla, T68)
    with op.batch_alter_table("videos", schema=None) as batch_op:
        batch_op.drop_constraint(_OLD, type_="check")
        batch_op.create_check_constraint(_OLD, _NEW_SQL)


def downgrade() -> None:
    # Reversa solo posible si no quedan filas 'pending' (limpieza previa)
    op.execute("UPDATE videos SET status='failed', error_message='downgrade' WHERE status='pending'")
    with op.batch_alter_table("videos", schema=None) as batch_op:
        batch_op.drop_constraint(_OLD, type_="check")
        batch_op.create_check_constraint(_OLD, _OLD_SQL)

"""download_archive mirror table model. Regla: 3.6.

Queryable mirror of the append-only <DATA_DIR>/download_archive.txt; the parser
accepts both line formats ("tiktok <id>" and bare "<id>", last token wins,
T-DB-8/T-DEPLOY-15) and mirrors the parsed ids here.
"""

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from tikdown_rs.models import Base


class DownloadArchive(Base):
    """One mirrored archive entry."""

    __tablename__ = "download_archive"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tiktok_id: Mapped[str] = mapped_column(Text, nullable=False)
    account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("monitored_accounts.id"), nullable=True
    )
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)

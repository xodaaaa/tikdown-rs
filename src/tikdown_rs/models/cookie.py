"""cookies model. Regla: 3.4.

cookie_blob is LargeBinary NOT NULL (never Text): the Netscape file is stored
unencrypted raw. Regla: 15.2.
"""

from sqlalchemy import CheckConstraint, Integer, LargeBinary, Text
from sqlalchemy.orm import Mapped, mapped_column

from tikdown_rs.models import Base


class Cookie(Base):
    """A cookie file (label + raw Netscape blob) with its validation lifecycle."""

    __tablename__ = "cookies"
    __table_args__ = (
        CheckConstraint(
            "validation_state IN ('valid', 'invalid', 'inconclusive')",
            name="ck_cookies_validation_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    cookie_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expiration_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Only updated with valid/invalid; inconclusive never touches it (13.2).
    last_validated_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_state: Mapped[str] = mapped_column(Text, nullable=False)
    last_validation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str | None] = mapped_column(Text, nullable=True)

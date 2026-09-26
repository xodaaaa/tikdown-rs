"""Models package: SQLAlchemy ORM models and the shared declarative Base."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all TikDown-rs tables."""

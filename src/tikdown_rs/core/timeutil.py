"""Shared timestamp helpers: ISO8601 strings for UTC-stamped columns."""

from datetime import UTC, datetime


def utcnow_iso() -> str:
    """ISO8601 UTC timestamp with explicit offset (timestamp columns)."""
    return datetime.now(UTC).isoformat()

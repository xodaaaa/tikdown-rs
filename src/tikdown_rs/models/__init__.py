"""Models package: SQLAlchemy ORM models and the shared declarative Base."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all TikDown-rs tables."""


from tikdown_rs.models.backfill_slot import (
    BackfillSlot,
    acquire_backfill_slot,
    release_backfill_slot,
)
from tikdown_rs.models.cookie import Cookie
from tikdown_rs.models.daemon_state import DaemonState, ensure_daemon_state_row
from tikdown_rs.models.download_pacing_state import (
    DownloadPacingState,
    reserve_download_slot,
)
from tikdown_rs.models.monitored_account import MonitoredAccount
from tikdown_rs.models.video import Video

__all__ = [
    "BackfillSlot",
    "Base",
    "Cookie",
    "DaemonState",
    "DownloadPacingState",
    "MonitoredAccount",
    "Video",
    "acquire_backfill_slot",
    "ensure_daemon_state_row",
    "release_backfill_slot",
    "reserve_download_slot",
]

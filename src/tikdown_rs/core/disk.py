"""Disk free-space probes and the 8.2 download-pause state machine.

Trampas neutralizadas: T-DEPLOY-21, T-ENGINE-27. Reglas: §10.1, §13.1, §8.2.
Tests must MOCK ``shutil.disk_usage`` (or inject ``disk_usage_fn``) with a
controlled free percentage, never hit the real disk.
"""

import shutil
from pathlib import Path

from tikdown_rs.core.daemon_state import read_status, set_downloads_paused
from tikdown_rs.core.notifications.events import (
    EVENT_DISK_PAUSED,
    EVENT_DISK_RESUMED,
    EVENT_DISK_WARNING,
)


def _free_percent(usage) -> float:
    return usage.free / usage.total * 100 if usage.total else 0.0


def free_percent(path) -> float:
    """Percentage of free space on the filesystem holding ``path`` (T-DEPLOY-21)."""
    return _free_percent(shutil.disk_usage(path))


def is_disk_ok(path, threshold_percent) -> bool:
    """True only when free percent is STRICTLY greater than the threshold (§10.1)."""
    return free_percent(path) > threshold_percent


def _backups_bytes(data_dir) -> int:
    """Sum of file sizes under ``<data_dir>/backups`` (8.2), 0 when absent.

    Retain-count bounded (11.1 SYSTEM_BACKUP_RETAIN_COUNT), so a plain walk
    suffices. Informational: on the same filesystem disk_usage already
    reflects it; the check REPORTS it so the operator sees the culprit.
    """
    backups = Path(data_dir) / "backups"
    if not backups.is_dir():
        return 0
    return sum(item.stat().st_size for item in backups.rglob("*") if item.is_file())


async def check_disk_pause(settings, session_factory, on_event=None, disk_usage_fn=None) -> bool:
    """One 900 s disk check driving downloads_paused (8.2). Returns the state.

    Under DISK_WARNING_FREE_PERCENT -> downloads_paused=1 + disk.warning +
    disk.paused, emitted ONCE per state change (dedupe by reading the
    previous state from daemon_state, not every check). Space recovered above
    the threshold with the pause set -> auto-resume + disk.resumed. The disk
    path is the only writer of the flag in this unit, so the resume is
    unambiguous (T-ENGINE-27 reason discipline lives in set_downloads_paused).

    ``disk_usage_fn`` is injectable for tests (T-DEPLOY-21). ``on_event`` is
    the SYNC channel (T-BACKFILL-15); may be None.
    """
    usage_fn = disk_usage_fn if disk_usage_fn is not None else shutil.disk_usage
    usage = usage_fn(settings.data_dir)
    percent = _free_percent(usage)
    backups = _backups_bytes(settings.data_dir)
    low = percent <= settings.disk_warning_free_percent  # NOT is_disk_ok (strict >)

    async with session_factory() as session:
        state = await read_status(session)
        was_paused = bool(state.downloads_paused) if state is not None else False
        if low:
            if not was_paused:
                if on_event is not None:
                    on_event(
                        {
                            "event": EVENT_DISK_WARNING,
                            "path": str(settings.data_dir),
                            "free_percent": percent,
                            "backups_bytes": backups,
                        }
                    )
                    on_event({"event": EVENT_DISK_PAUSED, "free_percent": percent})
                await set_downloads_paused(session, True, reason="disk")
            paused = True
        else:
            if was_paused:
                if on_event is not None:
                    on_event({"event": EVENT_DISK_RESUMED, "free_percent": percent})
                await set_downloads_paused(session, False, reason="disk")
            paused = False
    return paused

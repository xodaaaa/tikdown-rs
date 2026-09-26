"""Disk free-space probes for selfcheck and daemon healthcheck.

Trampas neutralizadas: T-DEPLOY-21. Regla: §10.1.
Tests must MOCK ``shutil.disk_usage`` with a controlled free percentage,
never hit the real disk.
"""

import shutil


def free_percent(path) -> float:
    """Percentage of free space on the filesystem holding ``path`` (T-DEPLOY-21)."""
    usage = shutil.disk_usage(path)
    if not usage.total:
        return 0.0
    return usage.free / usage.total * 100


def is_disk_ok(path, threshold_percent) -> bool:
    """True only when free percent is STRICTLY greater than the threshold (§10.1)."""
    return free_percent(path) > threshold_percent

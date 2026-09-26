"""core.disk probes: controlled free percentage, never the real disk.

Trampas neutralizadas: T-DEPLOY-21 (shutil.disk_usage is MOCKED with a
controlled percentage; no test may depend on the host disk). Regla: §10.1,
§13.1.
"""

import shutil
from collections import namedtuple

import pytest

from tikdown_rs.core.disk import free_percent, is_disk_ok

Usage = namedtuple("usage", ("total", "used", "free"))


def _mock_disk_usage(free_percent: float, total: int = 1_000_000):
    def fake(path):
        free = total * free_percent / 100
        return Usage(total=total, used=total - free, free=free)

    return fake


def test_free_percent_returns_controlled_math(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "disk_usage", _mock_disk_usage(25.0))
    assert free_percent("anywhere") == pytest.approx(25.0)


def test_is_disk_ok_threshold_is_strictly_greater(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exactly at the threshold is NOT ok (§10.1: free > disk_warning_free_percent)."""
    monkeypatch.setattr(shutil, "disk_usage", _mock_disk_usage(10.0))
    assert not is_disk_ok("anywhere", 10)

    monkeypatch.setattr(shutil, "disk_usage", _mock_disk_usage(10.1))
    assert is_disk_ok("anywhere", 10)

    monkeypatch.setattr(shutil, "disk_usage", _mock_disk_usage(9.9))
    assert not is_disk_ok("anywhere", 10)

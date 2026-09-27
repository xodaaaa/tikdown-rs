"""Selfcheck service: compose core probes and persist the result (daemon_state).

Trampas neutralizadas: T-DEPLOY-10, T-DEPLOY-21, T-DB-13, T-DB-14. Regla:
4.1, 10.1, 10.2.

Services are pure (plan 10.2): no cli/, daemon/ or yt_dlp imports here. The
impersonation probe lives in core.verify (core MAY import yt_dlp) and is
INJECTED, so this module never imports yt_dlp even transitively.
"""

import shutil
from dataclasses import dataclass
from typing import NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core import verify
from tikdown_rs.core.config import Settings
from tikdown_rs.core.daemon_state import record_selfcheck


@dataclass(frozen=True)
class SelfcheckResult:
    """One selfcheck run, for the CLI to print and for persistence checks."""

    impersonation_available: bool
    impersonation_reason: str
    impersonation_target_count: int
    ffmpeg_found: bool
    ffprobe_found: bool
    data_dir_writable: bool
    ok: bool
    degraded_reason: str | None


def _data_dir_writable(settings: Settings) -> bool:
    """OS-level write probe on DATA_DIR (same pattern as config.validate_for_daemon)."""
    probe = settings.data_dir / ".tikdown_write_probe"
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return False
    return True


def _degraded_reason(
    impersonation: tuple[bool, str, int],
    ffmpeg_found: bool,
    ffprobe_found: bool,
    data_dir_writable: bool,
) -> str | None:
    """Short ASCII cause naming the failing part; NULL when everything is ok."""
    if not impersonation[0]:
        return f"impersonation: {impersonation[1]}"
    if not (ffmpeg_found and ffprobe_found):
        missing = "ffmpeg" if not ffmpeg_found else "ffprobe"
        return f"binaries: {missing} not found"
    if not data_dir_writable:
        return "data_dir: not writable"
    return None


class StartupProbes(NamedTuple):
    """5.1 step 7 startup probes: binaries + impersonation, NO selfcheck persistence.

    ``impersonation_reason`` is None when impersonation is AVAILABLE; otherwise
    it carries the probe cause ('curl_cffi-missing', ...).
    """

    ffmpeg_ok: bool
    ffprobe_ok: bool
    impersonation_reason: str | None


def probe_startup(
    impersonation_fn=None, ffmpeg_path_fn=None, ffprobe_path_fn=None
) -> StartupProbes:
    """Run the 5.1 step 7 capacity probes WITHOUT persisting anything.

    The daemon composes this with the dedicated daemon_state mutator
    (record_startup_probes): the degraded_reason write happens exactly once,
    and selfcheck fields are never touched at startup. Injectable probe/path
    callables follow the run_selfcheck T-DEPLOY-21 discipline; None resolves
    to the core default AT CALL TIME.
    """
    impersonation_fn = impersonation_fn or verify.probe_impersonation
    ffmpeg_path_fn = ffmpeg_path_fn or shutil.which
    ffprobe_path_fn = ffprobe_path_fn or shutil.which
    available, reason, _count = impersonation_fn()
    return StartupProbes(
        ffmpeg_ok=ffmpeg_path_fn("ffmpeg") is not None,  # T-DEPLOY-10
        ffprobe_ok=ffprobe_path_fn("ffprobe") is not None,
        impersonation_reason=None if available else reason,
    )


async def run_selfcheck(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    impersonation_fn=None,
    ffprobe_path_fn=None,
    ffmpeg_path_fn=None,
) -> SelfcheckResult:
    """Run all selfcheck probes and persist the outcome to daemon_state.

    impersonation_fn/ffprobe_path_fn/ffmpeg_path_fn are injected for tests
    (T-DEPLOY-21 discipline: no host-dependent probes in the deterministic
    tier); None resolves to the core default AT CALL TIME so tests can
    monkeypatch core.verify.probe_impersonation / shutil.which. All DB writes
    go through record_selfcheck, a short session with an immediate commit
    (T-DB-13, singleton mutator style of core.daemon_state).
    """
    impersonation_fn = impersonation_fn or verify.probe_impersonation
    ffprobe_path_fn = ffprobe_path_fn or shutil.which
    ffmpeg_path_fn = ffmpeg_path_fn or shutil.which
    impersonation = impersonation_fn()
    ffmpeg_found = ffmpeg_path_fn("ffmpeg") is not None  # T-DEPLOY-10
    ffprobe_found = ffprobe_path_fn("ffprobe") is not None
    writable = _data_dir_writable(settings)
    reason = _degraded_reason(impersonation, ffmpeg_found, ffprobe_found, writable)

    ok = reason is None
    async with session_factory() as session:
        await record_selfcheck(session, ok=ok, degraded_reason=reason)

    return SelfcheckResult(
        impersonation_available=impersonation[0],
        impersonation_reason=impersonation[1],
        impersonation_target_count=impersonation[2],
        ffmpeg_found=ffmpeg_found,
        ffprobe_found=ffprobe_found,
        data_dir_writable=writable,
        ok=ok,
        degraded_reason=reason,
    )

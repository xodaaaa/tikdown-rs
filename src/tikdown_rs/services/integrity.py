"""`videos integrity [usuario]`: read-only archive diagnostic (M6 T4, §14.5).

Plan §14.5 defines the command as "tamaño + SHA-256 + ffprobe" and the
backup/restore paragraph says to re-run it after restoring a snapshot. It is
a DIAGNOSTIC: this module never writes persisted state (no commits, no row
updates — §14.5 assigns it no write; §4.7 terminal state remains the sole
truth point in services/videos.py).

Checks per Video row with a resolved ``local_path``:
- Size: file exists and size > 0 (§4.6/§14.5); otherwise flagged ``missing``.
- SHA-256: streamed in 1 MiB chunks via ``_sha256_file`` (reused from the §4.7
  truth point), always inside ``to_thread`` (T-ASYNC-8) — videos can be large,
  the file is never read whole into memory.
- ffprobe: binary detection REUSES the selfcheck's approach (``shutil.which``,
  injectable as ``ffprobe_path_fn`` in services/selfcheck.py). When ffprobe is
  NOT installed the container check is SKIPPED with an explicit per-row marker
  (``container_ok=None``, ``ffprobe_skipped=True``): integrity still reports
  size + SHA-256, and a missing ffprobe is a WARNING, not a failure — unlike
  the §4.7 download-time truth point, where an absent ffprobe is an
  infrastructure error (ConfigurationError), a diagnostic must degrade
  gracefully and say so. When available, ffprobe runs through the repo's
  subprocess pattern (sync ``subprocess.run`` inside ``asyncio.to_thread``,
  same as services/videos.py T-ASYNC-8) and only the container-parse exit
  code is recorded — no metadata dump.

Summary categories are disjoint and sum to ``total_checked``: ok, missing,
container_failed, ffprobe_skipped. Report types are plain/JSON-safe
(consistent with services/status.py, M6 T1).
"""

import asyncio
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.models import MonitoredAccount, Video
from tikdown_rs.services.videos import _normalize_username, _sha256_file


@dataclass
class IntegrityRow:
    """Per-video diagnostic result; plain JSON-safe types only."""

    tiktok_video_id: str
    username: str | None
    local_path: str
    missing: bool
    size: int | None
    sha256: str | None
    container_ok: bool | None  # None when ffprobe is unavailable (skipped)
    ffprobe_skipped: bool


@dataclass
class IntegrityReport:
    """§14.5 integrity snapshot: disjoint summary counts + per-row results."""

    total_checked: int
    ok: int
    missing: int
    container_failed: int
    ffprobe_skipped: int
    rows: list[IntegrityRow] = field(default_factory=list)


def _ffprobe_container(path: Path) -> bool:
    """Validate the container parses (§14.5): exit 0 = ok; no metadata dump.

    ``--`` BEFORE the path (T-ENGINE-24) so a '-'-leading file name cannot be
    parsed as an option.
    """
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "--", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


async def check_integrity(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    username: str | None = None,
    ffprobe_path_fn=shutil.which,
    ffprobe_fn=_ffprobe_container,
) -> IntegrityReport:
    """Read-only integrity scan of the archive (M6 T4, §14.5).

    ``ffprobe_path_fn`` and ``ffprobe_fn`` are injectable so callers (and
    tests) never depend on the real binary. Read-only: no session is ever
    committed, no Video row is modified.
    """
    query = (
        select(Video.tiktok_video_id, MonitoredAccount.username, Video.local_path)
        .outerjoin(MonitoredAccount, Video.account_id == MonitoredAccount.id)
        .where(Video.local_path.is_not(None))
        .order_by(Video.id)
    )
    if username is not None:
        query = query.where(MonitoredAccount.username == _normalize_username(username))

    ffprobe_available = ffprobe_path_fn("ffprobe") is not None
    rows: list[IntegrityRow] = []
    summary = {"ok": 0, "missing": 0, "container_failed": 0, "ffprobe_skipped": 0}
    async with session_factory() as session:
        result = await session.execute(query)
        for video_id, account_name, local_path in result.all():
            path = Path(local_path)
            try:
                size = path.stat().st_size
            except OSError:
                size = None
            missing = size is None or size <= 0
            sha256 = None if missing else await asyncio.to_thread(_sha256_file, path)
            container_ok: bool | None = None
            ffprobe_skipped = False
            if not missing:
                if not ffprobe_available:
                    ffprobe_skipped = True  # WARNING: size + SHA still reported
                else:
                    container_ok = await asyncio.to_thread(ffprobe_fn, path)
            row = IntegrityRow(
                tiktok_video_id=video_id,
                username=account_name,
                local_path=local_path,
                missing=missing,
                size=size,
                sha256=sha256,
                container_ok=container_ok,
                ffprobe_skipped=ffprobe_skipped,
            )
            rows.append(row)
            if missing:
                summary["missing"] += 1
            elif ffprobe_skipped:
                summary["ffprobe_skipped"] += 1
            elif not container_ok:
                summary["container_failed"] += 1
            else:
                summary["ok"] += 1
    return IntegrityReport(
        total_checked=len(rows),
        ok=summary["ok"],
        missing=summary["missing"],
        container_failed=summary["container_failed"],
        ffprobe_skipped=summary["ffprobe_skipped"],
        rows=rows,
    )

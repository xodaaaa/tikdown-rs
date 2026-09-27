"""Daemon status service: §10.1 gather + shared ASCII formatting (M6 T1).

La regla de oro (§10.2): the real logic lives in services/*; ``cli/`` and the
bot dispatcher only orchestrate. ``gather_status`` collects the §10.1
``daemon status`` fields (heartbeat, monitor, last selfcheck, supervised
tasks, zombie yt-dlp threads, contention counter read from ``daemon_state``
(T-DB-14), cookies by state, disk, recent errors derived from the ``videos``
table — no new table, §0.4) into the plain-typed ``DaemonStatus``, kept
JSON-ready for the §10.3 Column 1 static dashboard reuse (T6).
``format_status_lines`` renders the shared pure-ASCII output (T-CLI-1) used
by ``daemon status`` and the bot's /status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tikdown_rs.core import disk
from tikdown_rs.core.daemon_state import read_status
from tikdown_rs.models import Cookie, Video

RECENT_ERROR_LIMIT = 5  # 10.1: 'ultimos errores', a bounded tail
_ERROR_MESSAGE_MAX_CHARS = 120


def heartbeat_age_seconds(
    last_heartbeat_at: str | None, now: datetime | None = None
) -> float | None:
    """Seconds since the last heartbeat, or None when unknown."""
    if not last_heartbeat_at:
        return None
    last = datetime.fromisoformat(last_heartbeat_at)
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return ((now or datetime.now(UTC)) - last).total_seconds()


@dataclass
class DaemonStatus:
    """§10.1 status snapshot: plain types only, JSON-ready (§10.3 Column 1).

    Honesty contract (T-ASYNC-14): ``supervised_tasks`` and
    ``ytdlp_zombie_threads`` are IN-PROCESS counters; ``None`` means the
    caller cannot see them (out-of-process) and the ASCII formatter prints
    'n/a (in-process)' instead of a fake 0.
    """

    heartbeat_age_seconds: float | None
    monitor_running: bool
    daemon_pid: int | None
    db_busy_count_5min: int
    supervised_tasks: int | None = None
    ytdlp_zombie_threads: int | None = None
    cookie_counts: dict[str, int] = field(default_factory=dict)
    last_selfcheck_at: str | None = None
    last_selfcheck_ok: bool | None = None
    degraded_reason: str | None = None
    recent_errors: list[str] = field(default_factory=list)
    # Gathered only when the caller passes a data_dir (the dashboard's Column 1);
    # the CLI/bot outputs do not change (§10.1 wording is pinned by tests).
    disk_free_percent: float | None = None
    downloads_paused: bool = False
    # Optional static info (e.g. yt-dlp version) supplied by the caller.
    ytdlp_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Explicit plain-typed projection: no datetime objects, JSON-ready."""
        return {
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "monitor_running": self.monitor_running,
            "daemon_pid": self.daemon_pid,
            "db_busy_count_5min": self.db_busy_count_5min,
            "supervised_tasks": self.supervised_tasks,
            "ytdlp_zombie_threads": self.ytdlp_zombie_threads,
            "cookie_counts": dict(self.cookie_counts),
            "last_selfcheck_at": self.last_selfcheck_at,
            "last_selfcheck_ok": self.last_selfcheck_ok,
            "degraded_reason": self.degraded_reason,
            "recent_errors": list(self.recent_errors),
            "disk_free_percent": self.disk_free_percent,
            "downloads_paused": self.downloads_paused,
            "ytdlp_version": self.ytdlp_version,
        }


def status_from_row(
    row: Any,
    cookie_counts: dict[str, int] | None = None,
    *,
    supervised_tasks: int | None = None,
    ytdlp_zombie_threads: int | None = None,
    recent_error_lines: list[str] | None = None,
    now: datetime | None = None,
) -> DaemonStatus:
    """Map a daemon_state row (+ gathered side data) into the structured status."""
    return DaemonStatus(
        heartbeat_age_seconds=heartbeat_age_seconds(row.last_heartbeat_at, now),
        monitor_running=bool(row.monitor_running),
        daemon_pid=row.daemon_pid,
        db_busy_count_5min=row.db_busy_count_5min,
        supervised_tasks=supervised_tasks,
        ytdlp_zombie_threads=ytdlp_zombie_threads,
        cookie_counts=dict(cookie_counts or {}),
        last_selfcheck_at=row.last_selfcheck_at,
        last_selfcheck_ok=row.last_selfcheck_ok,
        degraded_reason=row.degraded_reason,
        recent_errors=list(recent_error_lines or []),
        downloads_paused=bool(row.downloads_paused),
    )


async def recent_error_lines(session: AsyncSession) -> list[str]:
    """10.1: last failed videos derived from the `videos` table, NO new table.

    A separate CLI process cannot reach the daemon's log stream, so the DB
    trace is the honest shared source: error_category + truncated message.
    """
    rows = (
        (
            await session.execute(
                select(Video)
                .where(Video.status == "failed")
                .order_by(Video.updated_at.desc())
                .limit(RECENT_ERROR_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return ["recent_errors: none"]
    lines = [f"recent_errors: {len(rows)}"]
    for video in rows:
        message = (video.error_message or "-")[:_ERROR_MESSAGE_MAX_CHARS]
        lines.append(
            f"recent_error: {video.tiktok_video_id} [{video.error_category or '-'}] {message}"
        )
    return lines


async def gather_status(
    session: AsyncSession,
    *,
    data_dir: str | None = None,
    now: datetime | None = None,
) -> DaemonStatus | None:
    """Gather the §10.1 status fields for `daemon status` and the bot's /status.

    Returns None when the daemon_state singleton row is absent (the daemon
    never ran); each frontend maps that to its own error surface. The
    in-process counters (supervised tasks, zombie threads) stay None here:
    callers holding the live objects (the daemon itself) may set them on the
    returned status before formatting (T-ASYNC-14).
    """
    row = await read_status(session)
    if row is None:
        return None
    counts = dict(
        (
            await session.execute(
                select(Cookie.validation_state, func.count()).group_by(Cookie.validation_state)
            )
        ).all()
    )
    status = status_from_row(
        row,
        counts,
        recent_error_lines=await recent_error_lines(session),
        now=now,
    )
    if data_dir is not None:
        status.disk_free_percent = disk.free_percent(data_dir)
    return status


def format_status_lines(status: DaemonStatus) -> list[str]:
    """10.1 status contents as plain ASCII key: value lines (T-CLI-1).

    Honesty contract (T-ASYNC-14): supervised tasks and zombie yt-dlp threads
    are IN-PROCESS counters of the daemon process. `daemon status` runs in a
    separate CLI process whose registry is always empty and whose engine
    object does not exist, so None prints 'n/a (in-process)' instead of a
    fake 0. A caller that DOES hold the live objects (the daemon itself or
    the M5 bot, which runs inside the daemon process) sets the real counts
    and gets real numbers.

    ponytail: persisting the counters would need new daemon_state columns
    (forbidden here: no migrations); the M5 bot or a future column can
    promote these to persisted values without changing the output format.
    """
    age = status.heartbeat_age_seconds
    if age is None:
        lines = ["heartbeat_age_seconds: unknown"]
    else:
        lines = [f"heartbeat_age_seconds: {age:.0f}"]
    lines.append(f"monitor_running: {int(status.monitor_running)}")
    lines.append(f"daemon_pid: {status.daemon_pid if status.daemon_pid is not None else 'none'}")
    # T-DB-14: the contention window is read from daemon_state (persisted by
    # the heartbeat), NEVER from this process (a CLI process always sees 0).
    lines.append(f"db_busy_count_5min: {status.db_busy_count_5min}")
    lines.append(
        f"supervised_tasks: {status.supervised_tasks if status.supervised_tasks is not None else 'n/a (in-process)'}"
    )
    lines.append(
        f"ytdlp_zombie_threads: {status.ytdlp_zombie_threads if status.ytdlp_zombie_threads is not None else 'n/a (in-process)'}"
    )
    lines.append(f"cookies_valid: {status.cookie_counts.get('valid', 0)}")
    lines.append(f"cookies_invalid: {status.cookie_counts.get('invalid', 0)}")
    lines.append(f"cookies_inconclusive: {status.cookie_counts.get('inconclusive', 0)}")
    lines.append(f"last_selfcheck_at: {status.last_selfcheck_at or 'none'}")
    lines.append(
        "last_selfcheck_ok: "
        + (
            str(int(status.last_selfcheck_ok))
            if status.last_selfcheck_ok is not None
            else "unknown"
        )
    )
    lines.append(f"degraded_reason: {status.degraded_reason or 'none'}")
    # 10.1 lists disk among the status fields (review finding M20): gathered
    # only when the caller passed data_dir; None prints 'unknown'.
    lines.append(
        f"disk_free_percent: {status.disk_free_percent:.1f}"
        if status.disk_free_percent is not None
        else "disk_free_percent: unknown"
    )
    lines.extend(status.recent_errors or ["recent_errors: none"])
    return lines

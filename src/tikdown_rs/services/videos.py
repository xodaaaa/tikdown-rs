"""Post-download integrity: the single truth point for terminal video state (4.7).

Trampas neutralizadas: T-ENGINE-5, T-ENGINE-18, T-ENGINE-24, T-ASYNC-8,
T-ASYNC-14/15, T-BACKFILL-13/14/15/21, T-DATA-1, T-DATA-3, T-DATA-10.
Reglas: 3.1, 3.3, 4.4, 4.7, 4.8.

``handle_download_result`` is the ONLY path that marks a video downloaded/
skipped/failed (3.3); monitor, backfill and retries all route through it.
Steps (4.7): (1) file exists and size > 0, (2) SHA-256 via ``to_thread``
(T-ASYNC-8), (3) ffprobe validation with ``--`` before the path (T-ENGINE-24),
(4) the ``total_disk_bytes`` increment rides the SAME commit as the video row
(3.1, T-DATA-10).

A file without a video stream has TWO distinct causes (T-ENGINE-5): an
expected slideshow (``expected_has_video=False`` -> skipped + dedupe add) or
a degraded response (real failure -> archive entry discarded, T-ENGINE-18;
persists as integrity failed). The §4.7 fallback retry lives INSIDE
``engine.download`` (DEFAULT_FORMAT -> FALLBACK_FORMAT funnel, archive entry
discarded before the fallback): a caller-side retry with the same formats
adds nothing, so the former ``retry_fn`` hook is retired (DR-18).

Layering (4.8): this module imports nothing from cli/, daemon/ or yt_dlp;
ffprobe/SHA arrive as injected callables. Engine download exceptions are the
CALLER's job to classify via ``persist_download_failure`` (T-DATA-3: the one
4.4 classifier, no parallels).
"""

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.archive import DownloadArchive
from tikdown_rs.core.daemon_state import set_downloads_paused
from tikdown_rs.core.errors import ConfigurationError, classify_error
from tikdown_rs.core.notifications import (
    EVENT_DOWNLOAD_DOWNLOADED,
    EVENT_DOWNLOAD_FAILED,
    EVENT_DOWNLOAD_SKIPPED,
)
from tikdown_rs.core.notifications.events import EVENT_DISK_PAUSED
from tikdown_rs.core.timeutil import utcnow_iso as _utcnow_iso
from tikdown_rs.models import MonitoredAccount, Video
from tikdown_rs.models.video import ErrorCategory, VideoStatus

logger = logging.getLogger("tikdown_rs.services.videos")

_RETRY_SUFFIX_RE = re.compile(r"\.retry-\d+$")
_MAX_ERROR_MESSAGE = 500

# --- read-only listing/export (M6 T2/T3, 10.1/10.2) ---------------------------

#: Default and ceiling for `videos last [N]` (bounded so a huge N cannot dump
#: the whole archive through the CLI/bot reply path).
DEFAULT_LAST_LIMIT = 10
MAX_LAST_LIMIT = 100

#: Shared row shape for recent_videos/export_videos: plain JSON-safe types.
#: Ordering column: ``downloaded_at`` (ISO-8601 UTC text, lexicographically
#: sortable), tie-broken by id DESC. The model has no ``uploaded_at``;
#: ``upload_date`` is canonical YYYYMMDD text (T-ENGINE-25), not a timestamp.
_ROW_FIELDS = (
    "id",
    "tiktok_video_id",
    "username",
    "status",
    "title",
    "description",
    "upload_date",
    "downloaded_at",
    "file_size",
    "local_path",
    "error_category",
    "error_message",
)

#: T-DEPLOY-16 formula-injection rule: a cell whose FIRST character is one of
#: = + - @ TAB CR is prefixed with a single quote so spreadsheet apps render
#: it as text instead of evaluating it as a formula.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _normalize_username(username: str) -> str:
    """Same normalization as accounts (3.1): strip '@', lowercase."""
    return username.strip().lstrip("@").strip().lower()


def _sanitize_csv_cell(value: str) -> str:
    """T-DEPLOY-16: prefix dangerous leading chars with a single quote."""
    if value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


async def _video_rows(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    username: str | None = None,
    limit: int | None = None,
    exclude_pending: bool = False,
) -> list[dict]:
    """Read-only shared query behind recent_videos/export_videos (10.2 golden rule).

    Plain dict rows, most-recent-first (see _ROW_FIELDS ordering note). A NULL
    downloaded_at (never-downloaded terminal row) sorts last.
    """
    query = (
        select(
            Video.id,
            Video.tiktok_video_id,
            MonitoredAccount.username,
            Video.status,
            Video.title,
            Video.description,
            Video.upload_date,
            Video.downloaded_at,
            Video.file_size,
            Video.local_path,
            Video.error_category,
            Video.error_message,
        )
        .outerjoin(MonitoredAccount, Video.account_id == MonitoredAccount.id)
        .order_by(Video.downloaded_at.desc().nulls_last(), Video.id.desc())
    )
    if username is not None:
        query = query.where(MonitoredAccount.username == _normalize_username(username))
    if exclude_pending:
        query = query.where(Video.status != VideoStatus.PENDING)
    if limit is not None:
        query = query.limit(limit)
    async with session_factory() as session:
        result = await session.execute(query)
        return [dict(row._mapping) for row in result.all()]


async def recent_videos(
    session_factory: async_sessionmaker[AsyncSession],
    limit: int = DEFAULT_LAST_LIMIT,
    username: str | None = None,
) -> list[dict]:
    """The LAST N processed videos, most-recent-first (M6 T2, 10.1).

    Pending (not yet processed) rows are excluded; a NULL downloaded_at sorts
    last. ``limit`` is clamped to [1, MAX_LAST_LIMIT]. Plain JSON-safe rows.
    """
    clamped = min(max(int(limit), 1), MAX_LAST_LIMIT)
    return await _video_rows(
        session_factory, username=username, limit=clamped, exclude_pending=True
    )


async def export_videos(
    session_factory: async_sessionmaker[AsyncSession],
    fmt: str,
    username: str | None = None,
) -> str:
    """Serialize the video archive for `videos export` (M6 T3, 10.1/10.2).

    T-CLI-3 parity: the payload carries NO Rich markup and NO wrapping; the
    caller prints it raw. JSON is a list of plain records; CSV uses the
    stdlib ``csv`` module with T-DEPLOY-16 formula sanitization (see
    _CSV_FORMULA_PREFIXES). Unknown formats raise ConfigurationError so the
    CLI funnels them as ERROR + exit 1 (T-CLI-4).
    """
    if fmt not in ("json", "csv"):
        raise ConfigurationError(f"invalid export format: {fmt} (expected json or csv)")
    rows = await _video_rows(session_factory, username=username)
    if fmt == "json":
        return json.dumps(rows, indent=2)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_ROW_FIELDS)
    for row in rows:
        writer.writerow(
            [
                _sanitize_csv_cell("" if row[field] is None else str(row[field]))
                for field in _ROW_FIELDS
            ]
        )
    return buffer.getvalue()


@dataclass(frozen=True)
class HandleResult:
    """Terminal outcome summary (4.7): outcome, category, final file path."""

    outcome: str  # 'downloaded' | 'skipped' | 'failed'
    error_category: str | None = None
    file_path: str | None = None


def _sha256_file(path: Path) -> str:
    """Step 2 (4.7): streaming SHA-256; ALWAYS run inside to_thread (T-ASYNC-8)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ffprobe_command(path: Path) -> list[str]:
    """ffprobe argv for the step-3 container check (4.7).

    T-ENGINE-33 (live round M6, TWO real traps in one command):
    1. ``--`` separates OPTIONS from INPUTS: every option token must precede
       it and the file must be LAST. With ``--`` right after ``v:0``, the
       trailing ``-show_entries``/``-of`` were parsed as INPUT FILENAMES and
       ffprobe exited 1 → the caller got ``{}``.
    2. ``-show_entries`` sections split on ``:`` (keys on ``,``):
       ``format=duration,stream=codec_name`` silently glues ``stream=...``
       into the format section's key list — the streams section is never
       emitted, ``_probe_has_video`` always False, EVERY download degraded to
       failed/integrity. Both traps are invisible to stubbed-ffprobe tests;
       only the real binary exposes them.
    """
    return [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "format=duration:stream=codec_name,width,height",
        "-of",
        "json",
        "--",
        str(path),
    ]


def _ffprobe_file(path: Path) -> dict:
    """Step 3 (4.7): default ffprobe runner.

    T-ENGINE-24: ``--`` BEFORE the path is mandatory -- a file name starting
    with '-' would otherwise be parsed as an ffprobe option. The argv itself
    is built by ``_ffprobe_command`` (T-ENGINE-33).
    """
    completed = subprocess.run(_ffprobe_command(path), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return {}
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}


def _probe_has_video(probe: dict) -> bool:
    """Validation rule (4.7): at least one video stream AND duration > 0.

    ``-select_streams v:0`` guarantees any returned stream IS a video stream.
    """
    if not (probe.get("streams") or []):
        return False
    try:
        return float((probe.get("format") or {}).get("duration")) > 0
    except (TypeError, ValueError):
        return False


def _rename_sync(source: Path, destination: Path) -> None:
    """Blocking rename, always called via ``to_thread`` (T-ASYNC-8)."""
    os.replace(source, destination)


async def _verify_integrity(path: Path, sha256_fn, ffprobe_fn) -> tuple[str, bool] | None:
    """Steps 1-3 (4.7). Returns ``(sha256, has_video)``, or None on step 1 failure.

    T-ENGINE-24: a missing ffprobe BINARY is an integrity-inconclusive
    infrastructure failure (the selfcheck records degraded binaries), not a
    video failure: it raises ConfigurationError instead of marking the row.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0:
        return None
    digest = await asyncio.to_thread(sha256_fn, path)  # step 2 (T-ASYNC-8)
    try:
        probe = await asyncio.to_thread(ffprobe_fn, path)
    except FileNotFoundError as exc:
        raise ConfigurationError(
            f"ffprobe binary not found; cannot verify integrity of {path} "
            "(infrastructure failure, not a video failure)"
        ) from exc
    return digest, _probe_has_video(probe)


def _emit(on_event, outcome: str, **fields) -> None:
    """Fire the event channel SYNCHRONOUSLY (T-BACKFILL-15: never wrap in async).

    Called exactly once per terminal outcome, on every path (T-BACKFILL-13:
    callers propagate on_event explicitly into every launched coroutine).
    Event names come from the catalog (6.2, T-DATA-9): no ad-hoc literals.
    """
    if on_event is None:
        return
    _OUTCOME_EVENT = {
        "downloaded": EVENT_DOWNLOAD_DOWNLOADED,
        "skipped": EVENT_DOWNLOAD_SKIPPED,
        "failed": EVENT_DOWNLOAD_FAILED,
    }
    on_event({"event": _OUTCOME_EVENT[outcome], **fields})


def _truncate(message: str) -> str:
    return message[:_MAX_ERROR_MESSAGE]


async def _persist_terminal(
    session_factory: async_sessionmaker[AsyncSession],
    video_row_id: int,
    *,
    status: str,
    error_category: str | None,
    error_message: str | None,
    retry_count: int,
    on_event,
    video_id: str | None,
    account_id: int | None,
    notify_on_download: bool,
    file_path: str | None = None,
    file_size: int | None = None,
    file_hash: str | None = None,
    bump_disk_bytes: bool = False,
) -> HandleResult:
    """Write terminal state (3.3) and fire the one terminal event.

    For a successful download the video row UPDATE and the
    ``monitored_accounts.total_disk_bytes`` increment share ONE commit (4.7
    step 4, T-DATA-10): never a separate step that can be lost mid-flight.
    """
    now = _utcnow_iso()
    async with session_factory() as session:
        row = await session.get(Video, video_row_id)
        if row is None:
            raise ConfigurationError(f"unknown video row id: {video_row_id}")
        row.status = status
        row.error_category = error_category
        row.error_message = _truncate(error_message) if error_message else None
        row.retry_count = retry_count
        row.updated_at = now
        if status == VideoStatus.DOWNLOADED:
            row.local_path = file_path
            row.file_size = file_size
            row.file_hash = file_hash
            row.downloaded_at = now
        if bump_disk_bytes and account_id is not None:
            await session.execute(
                text(
                    "UPDATE monitored_accounts"
                    " SET total_disk_bytes = total_disk_bytes + :size"
                    " WHERE id = :account_id"
                ),
                {"size": file_size, "account_id": account_id},
            )
        await session.commit()
    _emit(
        on_event,
        status,
        video_id=video_id,
        account_id=account_id,
        status=status,
        error_category=error_category,
        file_path=file_path,
        notify_on_download=notify_on_download,
    )
    return HandleResult(status, error_category, file_path)


async def handle_download_result(
    session_factory: async_sessionmaker[AsyncSession],
    video_row_id: int,
    downloaded_path: Path | str,
    video_id: str,
    account_id: int | None,
    *,
    base_retry_count: int = 0,
    expected_has_video: bool = True,
    notify_on_download: bool = False,
    on_event=None,
    ffprobe_fn=_ffprobe_file,
    sha256_fn=_sha256_file,
    archive: DownloadArchive | None = None,
) -> HandleResult:
    """Single truth point for terminal video state after a download (4.7).

    ``on_event`` is a SYNC callable fired once per terminal outcome
    (T-BACKFILL-15). The §4.7 degraded-response fallback retry lives INSIDE
    ``engine.download`` (DR-18); there is deliberately no caller-side
    ``retry_fn``. Engine download exceptions are classified by the CALLER via
    ``persist_download_failure``.

    The video row must already exist (created by discovery with
    status='pending', 3.3/T-DATA-1); an unknown id is a ConfigurationError.
    """
    async with session_factory() as session:
        if await session.get(Video, video_row_id) is None:
            raise ConfigurationError(f"unknown video row id: {video_row_id}")

    path = Path(downloaded_path)
    verified = await _verify_integrity(path, sha256_fn, ffprobe_fn)

    if verified is not None and not verified[1] and expected_has_video:
        # Degraded response (T-ENGINE-5 cause 2, real failure): the engine
        # funnel already exhausted DEFAULT_FORMAT and FALLBACK_FORMAT with the
        # archive entry discarded before the fallback (T-ENGINE-18). Discard
        # the archive entry so retry-failed can re-download, then persist
        # failed/integrity below (DR-18: no caller-side retry hook).
        if archive is not None:
            await archive.remove(video_id)
        verified = None

    if verified is not None and verified[1]:
        # Integrity OK: rename .retry-N to the FINAL outtmpl path BEFORE
        # persisting (T-ASYNC-15: rename only after integrity passes; the
        # zombie thread writing .retry-N can never collide with the final
        # name). The input path comes from the engine's data_dir-derived
        # outtmpl (T-BACKFILL-21), so stripping the retry suffix keeps the
        # path data_dir-derived and absolute.
        final = path.with_name(_RETRY_SUFFIX_RE.sub("", path.stem) + path.suffix)
        if final != path:
            await asyncio.to_thread(_rename_sync, path, final)
        final_str = os.path.abspath(final)
        return await _persist_terminal(
            session_factory,
            video_row_id,
            status=VideoStatus.DOWNLOADED,
            error_category=None,
            error_message=None,
            retry_count=base_retry_count,
            on_event=on_event,
            video_id=video_id,
            account_id=account_id,
            notify_on_download=notify_on_download,
            file_path=final_str,
            file_size=final.stat().st_size,
            file_hash=verified[0],
            bump_disk_bytes=True,
        )

    if verified is not None and not verified[1] and not expected_has_video:
        # Expected slideshow (T-ENGINE-5 cause 1): skipped + dedupe add,
        # NO retries, no error category.
        if archive is not None:
            await archive.add(video_id, account_id)
        return await _persist_terminal(
            session_factory,
            video_row_id,
            status=VideoStatus.SKIPPED,
            error_category=None,
            error_message=None,
            retry_count=base_retry_count,
            on_event=on_event,
            video_id=video_id,
            account_id=account_id,
            notify_on_download=notify_on_download,
        )

    # Failure: explicit integrity category persisted as status='failed'
    # (3.3: the status CHECK has no 'integrity' value; the category lives in
    # its own column).
    if verified is None:
        reason = "downloaded file is missing or empty"
    else:
        reason = "no video stream in downloaded file"
    return await _persist_terminal(
        session_factory,
        video_row_id,
        status=VideoStatus.FAILED,
        error_category=ErrorCategory.INTEGRITY,
        error_message=reason,
        retry_count=base_retry_count,
        on_event=on_event,
        video_id=video_id,
        account_id=account_id,
        notify_on_download=notify_on_download,
    )


async def persist_download_failure(
    session_factory: async_sessionmaker[AsyncSession],
    video_row_id: int,
    exc: BaseException,
    base_retry_count: int = 0,
    on_event=None,
) -> HandleResult:
    """Persist a download that RAISED, classified by THE classifier (4.4, T-DATA-3).

    The caller invokes this when ``engine.download`` itself raised; nothing in
    this module ever swallows an exception into a bare WARNING.

    'definitive'/'transient' persist directly. The classifier's 'local'
    (disk-full, T-ENGINE-27) and 'info' (empty account) categories have no
    CHECK value, so the video is still persisted as failed with the message
    kept and error_category NULL -- information is preserved, the CHECK is
    respected. The 'local' arm additionally takes the 8.2 actionable action:
    downloads_paused=1 + one disk.paused event; it NEVER counts for the
    circuit breaker nor touches cookies (T-ENGINE-27).

    ``on_event`` is the SYNC channel (T-BACKFILL-15), optional.
    """
    async with session_factory() as session:
        if await session.get(Video, video_row_id) is None:
            raise ConfigurationError(f"unknown video row id: {video_row_id}")

    category = classify_error(exc)
    if category not in (
        ErrorCategory.DEFINITIVE,
        ErrorCategory.TRANSIENT,
        ErrorCategory.INTEGRITY,
    ):
        logger.warning(
            "video %s failed with unstashable category '%s' (%s); "
            "persisting failed with error_category NULL",
            video_row_id,
            category,
            exc,
        )
        if category == "local":
            # T-ENGINE-27 (8.2): ENOSPC is LOCAL and actionable. Set after the
            # terminal persistence; never the breaker, never cookies.
            async with session_factory() as session:
                await set_downloads_paused(session, True, reason="disk")
            if on_event is not None:
                on_event({"event": EVENT_DISK_PAUSED})
        category = None
    message = str(exc) or repr(exc)
    return await _persist_terminal(
        session_factory,
        video_row_id,
        status=VideoStatus.FAILED,
        error_category=category,
        error_message=message,
        retry_count=base_retry_count,
        on_event=on_event,
        video_id=None,
        account_id=None,
        notify_on_download=False,
    )

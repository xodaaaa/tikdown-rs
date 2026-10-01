"""Monitor service: feed discovery + pending downloads for monitored accounts.

Trampas neutralizadas: T-BACKFILL-1, T-ENGINE-4, T-DATA-1. Regla: 3.3, 4.6,
5.8, 8.1, 8.2, 9.1.

Lifecycle (3.3): ``discover_new_videos`` lists the feed and INSERTs one
``status='pending'`` row per new video (the real migrated CHECK is exercised,
T-DATA-1); ``download_pendings`` takes the pendings, applies the cross-process
pacing (4.5) and routes EVERY terminal state through
``services/videos.handle_download_result`` -- the ONLY terminal-state writer.
One failed video never aborts the batch: it is recorded via
``persist_download_failure`` and the loop continues (T-DATA-1).

Throttle (4.6, T-BACKFILL-1): an account whose ``last_check_at IS NULL`` is
ALWAYS checked; a stored timestamp younger than ``THROTTLE_SECONDS`` skips the
listing entirely. ``upload_date`` absent entries receive the newest known
upload_date of THIS listing (T-BACKFILL-3: never NULL, never stale).

Listing failures are classified by THE classifier (4.4, T-DATA-3): 'info'
(empty account) and any listing that COMPLETED count as a completed check and
update ``last_check_at``; the transient extractor degradation 'keeps sending
the same page' (T-ENGINE-4) and every other transient/definitive failure do
NOT update it -- a degraded response is not a completed check, and keeping the
old timestamp makes the next cycle retry sooner. Only definitive auth failures
set ``needs_review``; a transient failure NEVER marks the account failed.

No cookies left (5.8): the monitor stops GLOBALLY
(``daemon_state.monitor_running = 0``) and ``monitor.stopped_no_cookies`` is
emitted; the same gate applies per ``run_monitor_cycle_once``.

Layering (4.8): nothing here imports cli/, daemon/ or yt_dlp; engine, pacer,
semaphore and archive arrive INJECTED and the event channel is SYNC
(T-BACKFILL-15), propagated explicitly to every helper (T-BACKFILL-13).
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.archive import DownloadArchive
from tikdown_rs.core.errors import ConfigurationError, classify_error
from tikdown_rs.core.notifications.events import (
    EVENT_MONITOR_STOPPED_NO_COOKIES,
    EVENT_MONITOR_VIDEO_DISCOVERED,
)
from tikdown_rs.core.timeutil import utcnow_iso as _utcnow_iso
from tikdown_rs.models import DaemonState, MonitoredAccount, Video
from tikdown_rs.models.video import VideoStatus
from tikdown_rs.services.cookies import get_working_cookie
from tikdown_rs.services.videos import handle_download_result, persist_download_failure

logger = logging.getLogger("tikdown_rs.services.monitor")

#: 11.2: throttle for account feed checks ('accounts check' and monitor cycles
#: share ONE constant). A NULL last_check_at is NEVER throttled (T-BACKFILL-1).
THROTTLE_SECONDS = 30

#: Bounded recent window for monitor cycles (4.6: the feed lists newest-first;
#: a monitor only needs the recent tail, not the full history backfill).
DEFAULT_MAX_ENTRIES = 30

_EMPTY_COUNTS = {"downloaded": 0, "skipped": 0, "failed": 0}


class _Pacer(Protocol):
    async def acquire(self) -> None: ...


def _emit_event(on_event, event: str, **fields) -> None:
    """Fire the SYNC channel with a catalog event name (6.2, T-BACKFILL-15)."""
    if on_event is None:
        return
    on_event({"event": event, **fields})


def _counts(**flags) -> dict:
    return {**_EMPTY_COUNTS, **flags}


async def _get_account(session_factory, username: str) -> MonitoredAccount:
    async with session_factory() as session:
        account = (
            (
                await session.execute(
                    select(MonitoredAccount).where(MonitoredAccount.username == username)
                )
            )
            .scalars()
            .first()
        )
    if account is None:
        raise ConfigurationError(f"unknown account: {username}")
    return account


async def _touch_last_check(session_factory, account_id: int) -> None:
    async with session_factory() as session:
        account = await session.get(MonitoredAccount, account_id)
        account.last_check_at = _utcnow_iso()
        await session.commit()


async def _mark_needs_review(session_factory, account_id: int) -> None:
    async with session_factory() as session:
        account = await session.get(MonitoredAccount, account_id)
        account.needs_review = True
        await session.commit()


async def set_monitor_running(session_factory, running: bool) -> None:
    """Persist daemon_state.monitor_running GLOBALLY (5.8, T-DB-12 upsert)."""
    async with session_factory() as session:
        await session.execute(
            sqlite_insert(DaemonState)
            .values(id=1, monitor_running=running)
            .on_conflict_do_update(index_elements=["id"], set_={"monitor_running": running})
        )
        await session.commit()


async def discover_new_videos(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
    *,
    engine,
    on_event=None,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> int:
    """List one account feed and INSERT a pending row per new video (3.3).

    Returns the count of NEW inserts. Throttle (T-BACKFILL-1): a NULL
    ``last_check_at`` is ALWAYS checked; a stored timestamp younger than
    ``THROTTLE_SECONDS`` skips the listing and returns 0.

    last_check_at policy (4.6): updated ONLY on a completed check -- a listing
    that returned (success, including an empty feed) or an 'info' response.
    A transient listing failure (T-ENGINE-4) or an auth failure keeps the old
    timestamp: a degraded response is not a completed check, and the next
    cycle retries sooner. A definitive auth failure additionally sets
    ``needs_review`` (the cycle then skips the account until reviewed).
    """
    account = await _get_account(session_factory, username)

    # Throttle (T-BACKFILL-1: NULL is treated as 'check now', never as 0s-skip).
    if account.last_check_at is not None:
        age = (datetime.now(UTC) - datetime.fromisoformat(account.last_check_at)).total_seconds()
        if age < THROTTLE_SECONDS:
            logger.debug("monitor.throttled: %s checked %.1fs ago", username, age)
            return 0

    try:
        # B1 (T-ASYNC-8): the listing is a blocking yt-dlp call (network +
        # sleep_interval_requests pagination) — never run it on the loop.
        entries = [
            e
            for e in await asyncio.to_thread(engine.list_videos, username, max_entries)
            if e.get("id")
        ]
    except Exception as exc:  # noqa: BLE001 - classified by THE classifier (T-DATA-3)
        category = classify_error(exc)
        if category == "info":
            # 4.4 rule 8: informative, counts for nothing, but the check
            # COMPLETED -- the account simply has no videos.
            await _touch_last_check(session_factory, account.id)
            logger.info("monitor.no_videos: %s (empty account)", username)
            return 0
        if category == "definitive":
            # Auth failure: review needed; last_check_at kept (not completed).
            await _mark_needs_review(session_factory, account.id)
            logger.error("monitor.auth_failure: %s needs review (%s)", username, exc)
            return 0
        # Transient (T-ENGINE-4: 'keeps sending the same page' is here, like
        # every other non-definitive failure). NEVER mark the account failed.
        logger.warning(
            "monitor.listing_transient: %s listing failed (%s): %s; "
            "last_check_at kept so the next cycle retries sooner",
            username,
            category,
            exc,
        )
        return 0

    # T-BACKFILL-3: the fallback for absent dates is the newest known
    # upload_date of THIS listing (the feed is newest-first), never NULL.
    newest_date = max((e.get("upload_date") or "" for e in entries), default="")
    discovered = 0
    for entry in entries:
        video_id = entry["id"]
        async with session_factory() as session:
            exists = (
                await session.execute(
                    select(Video.id).where(Video.tiktok_video_id == video_id).limit(1)
                )
            ).scalar_one_or_none()
        if exists is not None:
            continue
        canonical_url = entry.get("url") or (f"https://www.tiktok.com/@{username}/video/{video_id}")
        now = _utcnow_iso()
        async with session_factory() as session:
            session.add(
                Video(
                    tiktok_video_id=video_id,
                    account_id=account.id,
                    url=canonical_url,
                    title=entry.get("title"),
                    description=entry.get("description"),
                    duration=entry.get("duration"),
                    upload_date=entry.get("upload_date") or newest_date or "",
                    status=VideoStatus.PENDING,  # 3.3/T-DATA-1: real CHECK, real INSERT
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
        discovered += 1
        _emit_event(
            on_event,
            EVENT_MONITOR_VIDEO_DISCOVERED,
            username=username,
            video_id=video_id,
            title=entry.get("title"),
            url=canonical_url,
        )

    await _touch_last_check(session_factory, account.id)
    return discovered


async def download_pendings(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
    *,
    engine,
    pacer: _Pacer,
    semaphore: asyncio.Semaphore,
    archive: DownloadArchive,
    network_available: asyncio.Event | None = None,
    on_event=None,
    ffprobe_fn: Callable | None = None,
    sha256_fn: Callable | None = None,
) -> dict:
    """Download the account's pending rows through the one truth point (3.3).

    Gates (in order): no working cookie -> GLOBAL monitor stop (5.8,
    ``monitor_running = 0`` + ``monitor.stopped_no_cookies`` + ``stopped``);
    ``daemon_state.downloads_paused = 1`` -> immediate return (8.2); an
    injected ``network_available`` event that is not set -> immediate return
    (8.1). Then every pending row (``created_at`` ASC) goes through
    ``pacer.acquire()`` -> semaphore -> ``engine.download`` ->
    ``handle_download_result`` with ``on_event`` propagated (T-BACKFILL-13).
    Any per-video exception is recorded by ``persist_download_failure`` and
    the batch continues (T-DATA-1).

    Returns counts ``{downloaded, skipped, failed}`` plus flags ``stopped``
    and ``paused``.
    """
    # Gate 1 (5.8): the LAST working cookie gone stops the monitor GLOBALLY.
    if await get_working_cookie(session_factory) is None:
        await set_monitor_running(session_factory, False)
        _emit_event(on_event, EVENT_MONITOR_STOPPED_NO_COOKIES)
        logger.error("monitor.stopped_no_cookies: no working cookie left")
        return _counts(stopped=True)

    # Gate 2 (8.2): a global disk pause pauses every download; the flag is
    # RE-READ per call, never cached (T-ASYNC-16).
    async with session_factory() as session:
        state = await session.get(DaemonState, 1)
    if state is not None and state.downloads_paused:
        logger.info("monitor.downloads_paused: skipping %s (disk pause)", username)
        return _counts(paused=True)

    # Gate 3 (8.1): network offline pauses downloads.
    if network_available is not None and not network_available.is_set():
        logger.info("monitor.network_offline: skipping %s", username)
        return _counts(paused=True)

    account = await _get_account(session_factory, username)
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(Video)
                    .where(Video.account_id == account.id, Video.status == VideoStatus.PENDING)
                    .order_by(Video.created_at.asc())
                )
            )
            .scalars()
            .all()
        )

    counts = _counts()
    for row in rows:
        try:
            await pacer.acquire()  # 4.5: the one cross-process gate
            async with semaphore:
                path = await engine.download(
                    row.url or f"https://www.tiktok.com/@{username}/video/{row.tiktok_video_id}",
                    row.tiktok_video_id,
                    username,
                    archive=archive,
                )
            extra = {}
            if ffprobe_fn is not None:
                extra["ffprobe_fn"] = ffprobe_fn
            if sha256_fn is not None:
                extra["sha256_fn"] = sha256_fn
            result = await handle_download_result(
                session_factory,
                row.id,
                path,
                row.tiktok_video_id,
                account.id,
                # T-ENGINE-5 (4.7): a listing entry without duration is a
                # photo post; a file without a video stream is then the
                # EXPECTED slideshow -> skipped + dedupe add, never the
                # degraded-response fallback path.
                expected_has_video=row.duration is not None,
                notify_on_download=account.notify_on_download,
                on_event=on_event,  # T-BACKFILL-13: propagated EXPLICITLY
                archive=archive,
                **extra,
            )
            counts[result.outcome] += 1
        except Exception as exc:  # noqa: BLE001 - T-DATA-1: one failure, batch continues
            failure = await persist_download_failure(
                session_factory, row.id, exc, on_event=on_event
            )
            counts["failed"] += 1
            logger.warning(
                "monitor video %s failed (%s): %s",
                row.tiktok_video_id,
                failure.error_category,
                exc,
            )
    return counts


async def run_monitor_cycle_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    engine,
    pacer: _Pacer,
    semaphore: asyncio.Semaphore,
    archive: DownloadArchive,
    network_available: asyncio.Event | None = None,
    on_event=None,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    ffprobe_fn: Callable | None = None,
    sha256_fn: Callable | None = None,
) -> dict:
    """One full monitor heartbeat cycle over every active account.

    Discovers new videos for ALL accounts with ``mode='monitor'`` AND
    ``paused=0`` AND ``needs_review=0``, then downloads their pendings.
    Per-account isolation: one broken account logs and is skipped; the cycle
    continues (3.3, T-DATA-1 spirit). Global gates run ONCE before the loop:
    no working cookie stops the monitor GLOBALLY (5.8, one event instead of
    one per account); a disk pause (8.2) or offline network (8.1) skips the
    whole cycle.

    Returns aggregate counts: ``{discovered, downloaded, skipped, failed,
    accounts}`` plus flags ``stopped``/``paused``. This is the entry point
    the daemon heartbeat wires in M4 (T-DATA-2: no orphan jobs).

    Trampas neutralizadas: T-BACKFILL-1, T-ENGINE-4, T-DATA-1.
    Regla: 3.3, 4.6, 5.8.
    """
    # Global gate 1 (5.8): cookies ONCE, not once per account.
    if await get_working_cookie(session_factory) is None:
        await set_monitor_running(session_factory, False)
        _emit_event(on_event, EVENT_MONITOR_STOPPED_NO_COOKIES)
        logger.error("monitor.stopped_no_cookies: no working cookie left")
        return {**_counts(), "discovered": 0, "accounts": 0, "stopped": True}

    # Global gate 2 (8.2): re-read per cycle, never cached (T-ASYNC-16).
    async with session_factory() as session:
        state = await session.get(DaemonState, 1)
    if state is not None and state.downloads_paused:
        logger.info("monitor.downloads_paused: cycle skipped (disk pause)")
        return {**_counts(), "discovered": 0, "accounts": 0, "paused": True}

    # Global gate 3 (8.1).
    if network_available is not None and not network_available.is_set():
        logger.info("monitor.network_offline: cycle skipped")
        return {**_counts(), "discovered": 0, "accounts": 0, "paused": True}

    async with session_factory() as session:
        accounts = (
            (
                await session.execute(
                    select(MonitoredAccount)
                    .where(
                        MonitoredAccount.mode == "monitor",
                        MonitoredAccount.paused.is_(False),
                        MonitoredAccount.needs_review.is_(False),
                    )
                    .order_by(MonitoredAccount.id)
                )
            )
            .scalars()
            .all()
        )

    totals = {**_counts(), "discovered": 0, "accounts": 0, "stopped": False, "paused": False}
    for account in accounts:
        try:
            totals["discovered"] += await discover_new_videos(
                session_factory,
                account.username,
                engine=engine,
                on_event=on_event,
                max_entries=max_entries,
            )
            result = await download_pendings(
                session_factory,
                account.username,
                engine=engine,
                pacer=pacer,
                semaphore=semaphore,
                archive=archive,
                network_available=network_available,
                on_event=on_event,
                ffprobe_fn=ffprobe_fn,
                sha256_fn=sha256_fn,
            )
            for key in ("downloaded", "skipped", "failed"):
                totals[key] += result.get(key, 0)
            totals["accounts"] += 1
        except Exception:
            logger.exception("monitor cycle: account %s failed; continuing", account.username)
    return totals

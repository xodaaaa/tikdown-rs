"""Backfill service: resumable account history runs behind one cross-process slot (9).

Trampas neutralizadas: T-BACKFILL-2, T-BACKFILL-3, T-BACKFILL-5, T-BACKFILL-6,
T-BACKFILL-7, T-BACKFILL-8, T-BACKFILL-10, T-BACKFILL-11, T-BACKFILL-13,
T-BACKFILL-14, T-BACKFILL-18. Reglas: 9.1-9.6, 4.5, 4.6, 4.7.

Layering (4.8): nothing here imports cli/, daemon/ or yt_dlp. The engine, pacer
and concurrency semaphore arrive INJECTED; ``handle_download_result`` remains
the ONLY terminal-state writer (3.3) and ``classify_error`` the ONLY classifier
(T-DATA-3).

State machine (9.1): 'queued' --CAS slot--> 'backfilling' --> 'completed' |
'failed' (external) | 'paused' (disk/network cause or auth breaker, 9.6) |
'cancelled' (cooperative, 9.4) | 'queued' (CancelledError without a cause,
T-BACKFILL-6). The slot is released in EVERY branch (finally); it is never
left held.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.archive import DownloadArchive
from tikdown_rs.core.errors import AUTH_MARKERS, ConfigurationError, classify_error
from tikdown_rs.core.notifications import (
    EVENT_BACKFILL_CANCELLED,
    EVENT_BACKFILL_COMPLETED,
    EVENT_BACKFILL_NO_COOKIES,
    EVENT_BACKFILL_PAUSED,
    EVENT_BACKFILL_STARTED,
)
from tikdown_rs.models import (
    DaemonState,
    MonitoredAccount,
    Video,
    acquire_backfill_slot,
    release_backfill_slot,
)
from tikdown_rs.services.cookies import get_working_cookie
from tikdown_rs.services.videos import handle_download_result, persist_download_failure

logger = logging.getLogger("tikdown_rs.services.backfill")

_BREAKER_THRESHOLD = 5
_TERMINAL_VIDEO_STATUSES = ("downloaded", "skipped", "failed")


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _emit_event(on_event, event: str, **fields) -> None:
    """Fire the SYNC channel with a catalog event name (6.2, T-BACKFILL-15).

    A None channel (CLI foreground without notifications) stays silent.
    """
    if on_event is None:
        return
    on_event({"event": event, **fields})


class BackfillBreaker:
    """Per-account in-process auth breaker (9.6).

    Only definitive failures WITH auth markers count; transient/local/info
    failures reset the count. The counter lives in the process (reset on
    restart); the pauses it triggers persist in the database.
    """

    def __init__(self, threshold: int = _BREAKER_THRESHOLD) -> None:
        self.threshold = threshold
        self.consecutive_auth_failures = 0

    def record(self, exc: BaseException) -> bool:
        """Count one failure; return True when the breaker trips."""
        if classify_error(exc) == "definitive" and any(
            marker in str(exc).lower() for marker in AUTH_MARKERS
        ):
            self.consecutive_auth_failures += 1
        else:
            self.consecutive_auth_failures = 0
        return self.consecutive_auth_failures >= self.threshold


# Process-wide breaker state per account (9.6: counter in the process,
# pauses persist in the base). Keyed by account id.
_BREAKERS: dict[int, BackfillBreaker] = {}


async def _mark_backfilling(session_factory, account_id: int) -> None:
    """Conditional queued -> backfilling; a lost race is a ConfigurationError."""
    async with session_factory() as session:
        result = await session.execute(
            text(
                "UPDATE monitored_accounts SET backfill_status = 'backfilling',"
                " backfill_pause_reason = NULL, updated_at = :now"
                " WHERE id = :id AND backfill_status = 'queued'"
            ),
            {"now": _utcnow_iso(), "id": account_id},
        )
        await session.commit()
    if result.rowcount == 0:
        raise ConfigurationError(f"backfill.not_queued: account {account_id} is no longer 'queued'")


async def _persist_total(session_factory, account_id: int, total: int) -> bool:
    """Persist backfill_total AFTER the real listing (9.3, T-BACKFILL-5).

    Conditional (T-BACKFILL-7): rowcount 0 means a concurrent cancel won the
    row while the feed was being listed.
    """
    async with session_factory() as session:
        result = await session.execute(
            text(
                "UPDATE monitored_accounts SET backfill_total = :total, updated_at = :now"
                " WHERE id = :id AND backfill_status = 'backfilling'"
            ),
            {"total": total, "now": _utcnow_iso(), "id": account_id},
        )
        await session.commit()
    return result.rowcount == 1


async def _get_or_create_pending_video(
    session_factory, account_id: int, entry: dict
) -> tuple[int, str | None]:
    """Idempotent pending-row INSERT (3.3, T-DATA-1); returns (row_id, existing_status).

    An already-terminal row is returned as-is: a backfill run never re-processes
    a video that already has terminal state (retry-failed is a separate command,
    9.6/T-ENGINE-18).
    """
    video_id = entry["id"]
    canonical_url = entry.get("url") or (
        f"https://www.tiktok.com/@{entry.get('uploader')}/video/{video_id}"
    )
    async with session_factory() as session:
        existing = (
            (await session.execute(select(Video).where(Video.tiktok_video_id == video_id).limit(1)))
            .scalars()
            .first()
        )
        if existing is not None:
            return existing.id, existing.status
        now = _utcnow_iso()
        video = Video(
            tiktok_video_id=video_id,
            account_id=account_id,
            url=canonical_url,
            title=entry.get("title"),
            description=entry.get("description"),
            duration=entry.get("duration"),
            upload_date=entry.get("upload_date") or None,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        session.add(video)
        await session.commit()
        return video.id, None


async def _persist_progress(
    session_factory, account_id: int, cursor: str | None, done: int
) -> bool:
    """Conditional cursor/done advance (9.4, T-BACKFILL-7).

    rowcount 0 means a concurrent cancel won: the progress is NEVER written
    over a non-'backfilling' row.
    """
    async with session_factory() as session:
        result = await session.execute(
            text(
                "UPDATE monitored_accounts SET backfill_cursor = :cursor,"
                " backfill_done = :done, updated_at = :now"
                " WHERE id = :id AND backfill_status = 'backfilling'"
            ),
            {"cursor": cursor, "done": done, "now": _utcnow_iso(), "id": account_id},
        )
        await session.commit()
    return result.rowcount == 1


async def _pause_for_breaker(session_factory, account_id: int) -> str:
    """Auth-breaker pause: paused + needs_review, batch stops (9.6).

    ponytail: no pause_reason for the breaker pause -- the CHECK only allows
    disk/network and an auth pause is neither; needs_review carries the cause.
    """
    async with session_factory() as session:
        result = await session.execute(
            text(
                "UPDATE monitored_accounts SET paused = 1, needs_review = 1,"
                " backfill_status = 'paused', updated_at = :now"
                " WHERE id = :id AND backfill_status = 'backfilling'"
            ),
            {"now": _utcnow_iso(), "id": account_id},
        )
        await session.commit()
    return "paused" if result.rowcount == 1 else "cancelled"


async def transition_to_monitor_after_backfill(
    session_factory: async_sessionmaker[AsyncSession], account_id: int
) -> bool:
    """9.5: SAME-COMMIT history -> monitor transition (T-BACKFILL-16/18).

    One transaction, one commit: the account UPDATE and the
    ``daemon_state.monitor_running = 1`` activation (the same semantics as the
    ``monitor start`` helper) land together. Idempotent by condition: returns
    True only when the transition actually fired.
    """
    async with session_factory() as session:
        result = await session.execute(
            text(
                "UPDATE monitored_accounts SET mode = 'monitor',"
                " monitor_after_backfill = 0, updated_at = :now"
                " WHERE id = :id AND mode = 'history'"
                " AND monitor_after_backfill = 1 AND backfill_status = 'completed'"
            ),
            {"now": _utcnow_iso(), "id": account_id},
        )
        if result.rowcount != 1:
            return False  # nothing fired; nothing to commit
        # T-BACKFILL-18 in the SAME commit: native singleton upsert, so an
        # absent daemon_state row is bootstrapped here too (T-DB-12).
        await session.execute(
            sqlite_insert(DaemonState)
            .values(id=1, monitor_running=True)
            .on_conflict_do_update(index_elements=["id"], set_={"monitor_running": True})
        )
        await session.commit()
        return True


async def reconcile_pending_monitor_transitions(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Defensive startup pass (9.5, T-BACKFILL-16): replay pending transitions.

    The history->monitor UPDATE is written in the same commit as completion,
    but a crash between them must never strand the flag: startup replays the
    SAME idempotent transition (which also activates monitor_running,
    T-BACKFILL-18). Returns the number of transitions that actually fired.
    """
    async with session_factory() as session:
        ids = (
            (
                await session.execute(
                    select(MonitoredAccount.id).where(
                        MonitoredAccount.mode == "history",
                        MonitoredAccount.monitor_after_backfill.is_(True),
                        MonitoredAccount.backfill_status == "completed",
                    )
                )
            )
            .scalars()
            .all()
        )
    fired = 0
    for account_id in ids:
        if await transition_to_monitor_after_backfill(session_factory, account_id):
            fired += 1
    return fired


async def reconcile_stale_backfills(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Requeue orphaned 'backfilling' rows after a crash (9.1, T-BACKFILL-6).

    'paused' rows are NEVER touched (their pause cause needs resolving, not
    reconciling); daemon startup calls this in M4.
    """
    async with session_factory() as session:
        result = await session.execute(
            text(
                "UPDATE monitored_accounts SET backfill_status = 'queued', updated_at = :now"
                " WHERE backfill_status = 'backfilling'"
            ),
            {"now": _utcnow_iso()},
        )
        await session.commit()
        return result.rowcount or 0


async def collect_queued_backfills(
    session_factory: async_sessionmaker[AsyncSession],
    engine_factory_fn: Callable[[MonitoredAccount], object],
    network_online_fn: Callable[[], bool] = lambda: True,
) -> list[str]:
    """Report-only collectible-queue probe (9.1); M4's job wires the launch.

    Selects 'queued' accounts plus 'paused' accounts whose pause cause is
    RESOLVED (disk: daemon_state.downloads_paused=0; network: the injected
    probe). For each, the slot must be free (CAS probe with owner 'collect').

    ponytail: probe-acquire-then-release has a race window (freed slot can be
    taken between probe and M4's real acquire) -- acceptable for M3
    report-only semantics; M4 replaces the probe with the real run. M3 has no
    network state: the default probe reports online. ``engine_factory_fn`` is
    reserved for M4's supervised-task wiring and is intentionally unused here.
    """
    async with session_factory() as session:
        state = await session.get(DaemonState, 1)
        disk_pause_resolved = state is None or not state.downloads_paused
        rows = (
            (
                await session.execute(
                    select(MonitoredAccount)
                    .where(MonitoredAccount.backfill_status.in_(("queued", "paused")))
                    .order_by(MonitoredAccount.id)
                )
            )
            .scalars()
            .all()
        )

    usernames: list[str] = []
    for row in rows:
        if row.backfill_status == "paused":
            if row.backfill_pause_reason == "disk" and not disk_pause_resolved:
                continue
            if row.backfill_pause_reason == "network" and not network_online_fn():
                continue
        async with session_factory() as session:
            won = await acquire_backfill_slot(session, "collect")
        if not won:
            continue
        async with session_factory() as session:
            await release_backfill_slot(session, "collect")
        usernames.append(row.username)
    return usernames


async def run_backfill(
    session_factory: async_sessionmaker[AsyncSession],
    account_id: int,
    *,
    engine,
    pacer,
    semaphore,
    archive: DownloadArchive,
    max_entries: int | None = None,
    on_event=None,
    ffprobe_fn: Callable[[Path], dict] | None = None,
    sha256_fn: Callable[[Path], str] | None = None,
    network_online_fn: Callable[[], bool] | None = None,
) -> str:
    """Run one account backfill to a terminal backfill_status; returns the status.

    Gates (9.1): a working cookie MUST exist, the account MUST be 'queued' and
    the cross-process slot MUST be free (never two runs on one account,
    T-BACKFILL-20). Cancellation is cooperative (9.4): the caller cancels the
    task; ``run_backfill`` distinguishes the pause cause in
    ``except asyncio.CancelledError`` (a BaseException, never caught as
    Exception), transitions the state and RETURNS -- re-raising is the
    caller's decision, the state is already unwedged (T-BACKFILL-6).

    ``ffprobe_fn``/``sha256_fn`` are integrity-injection passthroughs for the
    single truth point (4.7); defaults are the real implementations.
    """
    # Gate 1 (9.1, T-BACKFILL-12): real cookies, never a silent default.
    if await get_working_cookie(session_factory) is None:
        _emit_event(on_event, EVENT_BACKFILL_NO_COOKIES, account_id=account_id)
        raise ConfigurationError("backfill.no_cookies: no working cookie")

    # Gate 2 (9.1, T-BACKFILL-20): cross-process CAS slot BEFORE the state
    # check, so the concurrent second run hits slot_busy -- the exact
    # two-backfills-on-one-account risk the slot exists to stop. NEVER
    # proceed busy.
    slot_owner = f"backfill:{account_id}"
    async with session_factory() as session:
        won = await acquire_backfill_slot(session, slot_owner)
    if not won:
        raise ConfigurationError("backfill.slot_busy: the backfill slot is held by another run")

    # Set by the try block; a CancelledError before that must still emit.
    username: str | None = None
    try:
        # Gate 3: only the queueing operation puts an account in 'queued'.
        async with session_factory() as session:
            account = await session.get(MonitoredAccount, account_id)
        if account is None:
            raise ConfigurationError(f"unknown account id: {account_id}")
        if account.backfill_status != "queued":
            raise ConfigurationError(
                f"backfill.not_queued: account {account.username} backfill_status is "
                f"'{account.backfill_status}', expected 'queued'"
            )
        username = account.username

        await _mark_backfilling(session_factory, account_id)
        _emit_event(on_event, EVENT_BACKFILL_STARTED, account_id=account_id, username=username)

        # Listing INSIDE the try (T-BACKFILL-6): a CancelledError during the
        # listing must unwedge the state exactly like one mid-download.
        entries = [e for e in engine.list_videos(username, max_entries) if e.get("id")]
        # T-BACKFILL-5: the total is computed and persisted AFTER the real
        # listing, never from a still-None variable.
        if not await _persist_total(session_factory, account_id, len(entries)):
            _emit_event(
                on_event, EVENT_BACKFILL_CANCELLED, account_id=account_id, username=username
            )
            return "cancelled"

        # T-BACKFILL-2: the SKIP comparison uses the SNAPSHOT taken before the
        # loop, never the moving cursor.
        async with session_factory() as session:
            account = await session.get(MonitoredAccount, account_id)
        scope_cursor = account.backfill_cursor
        moving_cursor = account.backfill_cursor
        done = account.backfill_done
        breaker = _BREAKERS.setdefault(account_id, BackfillBreaker())

        for entry in entries:
            # T-BACKFILL-10: re-read the status every video (N=1, one row).
            async with session_factory() as session:
                account = await session.get(MonitoredAccount, account_id)
            if account.backfill_status == "cancelled":
                # T-BACKFILL-8: early return, NO completed event, NO
                # --then-monitor transition.
                _emit_event(
                    on_event, EVENT_BACKFILL_CANCELLED, account_id=account_id, username=username
                )
                return "cancelled"

            upload_date = entry.get("upload_date") or ""
            # T-BACKFILL-2: the boundary uses the SNAPSHOT taken before the
            # loop, never the moving cursor (using the moving one stopped the
            # run after the first video: the next listed entry is always
            # older). §9.2 mandates a STRICTLY '<' comparison, never '=='
            # (B6/JD-B-006): the entry equal to the cursor is re-processed --
            # terminal rows skip the download below, and a retry-failed row
            # reset to 'pending' is reachable again (JD-A-006). The loop
            # breaks only at strictly older entries.
            if upload_date and scope_cursor and upload_date < scope_cursor:
                break

            row_id, existing_status = await _get_or_create_pending_video(
                session_factory, account_id, entry
            )
            # 9.3 (B6): done counts videos reaching a terminal state IN THIS
            # RUN; re-walked terminal rows on a resume download nothing and
            # must not inflate backfill_done.
            new_transition = existing_status not in _TERMINAL_VIDEO_STATUSES
            if new_transition:
                try:
                    await pacer.acquire()  # 4.5: the one cross-process gate
                    async with semaphore:
                        path = await engine.download(
                            entry.get("url")
                            or f"https://www.tiktok.com/@{username}/video/{entry['id']}",
                            entry["id"],
                            entry.get("uploader"),
                            retry_index=0,
                            archive=archive,
                        )
                    extra = {}
                    if ffprobe_fn is not None:
                        extra["ffprobe_fn"] = ffprobe_fn
                    if sha256_fn is not None:
                        extra["sha256_fn"] = sha256_fn
                    await handle_download_result(
                        session_factory,
                        row_id,
                        path,
                        entry["id"],
                        account_id,
                        notify_on_download=account.notify_on_download,  # T-BACKFILL-14
                        on_event=on_event,  # T-BACKFILL-13: propagated EXPLICITLY
                        archive=archive,
                        **extra,
                    )
                except Exception as exc:  # noqa: BLE001 - T-BACKFILL-11: ANY failure is per-video
                    # T-BACKFILL-11: one failed video NEVER aborts the feed;
                    # asyncio.CancelledError is a BaseException and passes
                    # through to the handler below untouched.
                    failure = await persist_download_failure(session_factory, row_id, exc)
                    logger.warning(
                        "backfill video %s failed (%s): %s",
                        entry["id"],
                        failure.error_category,
                        exc,
                    )
                    if breaker.record(exc):
                        logger.error(
                            "backfill breaker tripped for account %s after %d "
                            "consecutive auth failures",
                            username,
                            breaker.threshold,
                        )
                        return await _pause_for_breaker(session_factory, account_id)

            # Terminal only (9.2/9.3): downloaded/failed/skipped advance the
            # MOVING cursor (T-BACKFILL-3: an absent date keeps the previous
            # value; never the initial snapshot, never NULL-by-overwrite).
            if upload_date:
                moving_cursor = upload_date
            if new_transition:
                done += 1
            if not await _persist_progress(session_factory, account_id, moving_cursor, done):
                _emit_event(
                    on_event, EVENT_BACKFILL_CANCELLED, account_id=account_id, username=username
                )
                return "cancelled"  # T-BACKFILL-7: a concurrent cancel won

        async with session_factory() as session:
            finished = await session.execute(
                text(
                    "UPDATE monitored_accounts SET backfill_status = 'completed',"
                    " updated_at = :now"
                    " WHERE id = :id AND backfill_status = 'backfilling'"
                ),
                {"now": _utcnow_iso(), "id": account_id},
            )
            await session.commit()
        if finished.rowcount == 0:
            _emit_event(
                on_event, EVENT_BACKFILL_CANCELLED, account_id=account_id, username=username
            )
            return "cancelled"  # T-BACKFILL-8: cancel won; no transition
        _emit_event(
            on_event,
            EVENT_BACKFILL_COMPLETED,
            account_id=account_id,
            username=username,
            done=done,
            total=len(entries),
        )
        # 9.5: idempotent same-commit transition (safe to double-call).
        await transition_to_monitor_after_backfill(session_factory, account_id)
        return "completed"

    except asyncio.CancelledError:
        # 9.1 (T-BACKFILL-6): distinguish the cause, unwedge the state, return.
        async with session_factory() as session:
            state = await session.get(DaemonState, 1)
        cause: str | None = None
        if state is not None and state.downloads_paused:
            cause = "disk"
        elif network_online_fn is not None and not network_online_fn():
            cause = "network"
        status = "paused" if cause else "queued"
        async with session_factory() as session:
            await session.execute(
                text(
                    "UPDATE monitored_accounts SET backfill_status = :status,"
                    " backfill_pause_reason = :reason, updated_at = :now"
                    " WHERE id = :id AND backfill_status = 'backfilling'"
                ),
                {
                    "status": status,
                    "reason": cause,
                    "now": _utcnow_iso(),
                    "id": account_id,
                },
            )
            await session.commit()
        if status == "paused":
            _emit_event(
                on_event,
                EVENT_BACKFILL_PAUSED,
                account_id=account_id,
                username=username,
                reason=cause,
            )
        return status

    except ConfigurationError:
        # Gate rejections (no_cookies/not_queued/slot_busy) are fail-fast
        # business errors raised BEFORE 'backfilling' is ever set: re-raise
        # untouched, never unwedge (there is nothing to unwedge).
        raise

    except Exception:
        # B5 (JD-A-002): a non-cancel crash (transient listing failure, locked
        # DB, IntegrityError) used to leave the account in 'backfilling' with
        # no producer able to pick it up until a manual restart (collect only
        # takes queued/paused; reconcile only runs at startup). T-BACKFILL-6:
        # no identifiable cause -> 'queued', the state is unwedged.
        logger.exception("backfill of account %s crashed; unwedging to queued", account_id)
        async with session_factory() as session:
            await session.execute(
                text(
                    "UPDATE monitored_accounts SET backfill_status = 'queued',"
                    " updated_at = :now"
                    " WHERE id = :id AND backfill_status = 'backfilling'"
                ),
                {"now": _utcnow_iso(), "id": account_id},
            )
            await session.commit()
        return "failed"

    finally:
        async with session_factory() as session:
            await release_backfill_slot(session, slot_owner)

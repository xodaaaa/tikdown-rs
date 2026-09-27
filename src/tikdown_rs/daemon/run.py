"""Foreground daemon lifecycle: strict startup order (5.1), bounded shutdown (5.2).

Trampas neutralizadas: T-ASYNC-3, T-ASYNC-4, T-ASYNC-6, T-ASYNC-13,
T-ASYNC-16, T-BACKFILL-6, T-BACKFILL-13, T-BACKFILL-16, T-CLI-6, T-CLI-7,
T-DEPLOY-6, T-DEPLOY-8, T-DATA-2, T-DB-14, T-ENGINE-28. Reglas: 5.1, 5.2,
5.3, 5.5, 5.6, 9.5, 11.2.

Signal coverage: on POSIX, SIGINT and SIGTERM are installed with
``loop.add_signal_handler`` and route into the same shutdown path (stop_event).
On Windows, ``add_signal_handler`` is not supported by the event loop: SIGINT
arrives natively as KeyboardInterrupt, which asyncio.run converts into a
cancellation of the lifecycle coroutine, so the same finally-block shutdown path
runs; SIGTERM has no asyncio handler on Windows and is out of scope for M0
(plan 14.3 targets Docker/Linux, where SIGTERM is the stop signal).

M4 startup order (STRICT, 5.1):
1. validate_for_daemon (T-DEPLOY-8).
2. clear inherited stop_requested (T-CLI-7) - before anything else reads it.
3. run_migrations via asyncio.to_thread (T-ASYNC-4: Alembic's env.py calls
   asyncio.run internally, so it must never run on the loop thread).
4. setup_logging(force=True) immediately after migrations (T-DEPLOY-6: Alembic's
   fileConfig stomps the root logger).
5. build components: engine (needs a working cookie; NONE -> engine=None and
   the daemon stays ALIVE degraded, 4.1), archive, pacer, semaphore,
   NetworkMonitor (event created PRE-SET, T-ENGINE-7), notifications channel.
6. initial state: monitor starts stopped (monitor_running=0) unless
   MONITOR_AUTOSTART=true; startup reconciliations: defensive history->monitor
   transition replay (T-BACKFILL-16) + reconcile_stale_backfills (T-BACKFILL-6).
7. startup capacity probes: impersonation + ffmpeg/ffprobe (T-DEPLOY-10),
   persisted ONLY as degraded_reason via the dedicated mutator.
8. Telegram bot (6.1): built with INJECTED dependencies (T-BOT-3: the daemon's
   own session_factory, ONE engine), started with the strict
   initialize/start/start_polling sequence (T-BOT-1, never run_polling) and
   supervised (6.5) BEFORE the scheduler. An enhancement, not a critical
   path: a bot failure at startup only logs -- the daemon continues without it.
9. ALL 7 jobs of 5.3 registered (max_instances=1 + coalesce=True, T-ASYNC-13);
   every job leaves a consultable trace (T-DATA-2) and every flag a job reads
   is RE-READ per execution, never cached at registration (T-ASYNC-16).
10. await stop_event.

Shutdown (5.2): stop_event by signal or watcher (poll <= 0.5 s); scheduler
shutdown stops the *scheduling* (T-ASYNC-9); daemon.stopped emitted BEFORE the
drain (T-ASYNC-6); bounded drain (10 s) of supervised tasks (this cancels the
bot supervision loop); bot stopped with the strict T-BOT-1 sequence;
daemon_state cleanup; engine dispose.
"""

import asyncio
import logging
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass

import yt_dlp.version
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tikdown_rs.bot.dispatcher import TikDownBot
from tikdown_rs.bot.supervision import PollingSupervisor, start_bot_polling, stop_bot_polling
from tikdown_rs.core.archive import DownloadArchive
from tikdown_rs.core.config import Settings
from tikdown_rs.core.daemon_state import (
    clear_daemon_runtime,
    clear_stop_requested,
    read_status,
    read_stop_requested,
    record_last_known_good_ytdlp,
    record_startup_probes,
    register_daemon_start,
    write_heartbeat,
)
from tikdown_rs.core.db import (
    create_db_engine,
    db_busy_count_5min,
    make_session_factory,
    retry_on_locked,
    sqlite_url_for,
)
from tikdown_rs.core.disk import check_disk_pause
from tikdown_rs.core.download_engine import YtDlpEngine
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.core.logging import setup_logging
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.core.network_monitor import NetworkMonitor
from tikdown_rs.core.notifications import (
    EVENT_DAEMON_DB_CONTENTION,
    EVENT_DAEMON_STARTED,
    EVENT_DAEMON_STOPPED,
    EVENT_SELFCHECK_FAILED,
    EVENT_SELFCHECK_OK,
    NoopNotificationService,
    NotificationService,
)
from tikdown_rs.core.pacing import DownloadPacer, DownloadSemaphore
from tikdown_rs.core.tasks import create_supervised_task, drain_supervised_tasks
from tikdown_rs.core.verify import entries_have_video, probe_profile
from tikdown_rs.models import MonitoredAccount
from tikdown_rs.services.backfill import (
    collect_queued_backfills,
    reconcile_pending_monitor_transitions,
    reconcile_stale_backfills,
    run_backfill,
)
from tikdown_rs.services.cookies import get_working_cookie
from tikdown_rs.services.maintenance import refresh_all_profiles, validate_all_cookies
from tikdown_rs.services.monitor import run_monitor_cycle_once, set_monitor_running
from tikdown_rs.services.selfcheck import probe_startup, run_selfcheck
from tikdown_rs.services.static_site import render_site, resolve_output_dir

logger = logging.getLogger(__name__)

# 11.2 constants: stop_requested polling <= 0.5 s; shutdown drain 10 s.
STOP_POLL_INTERVAL_SECONDS = 0.5
SHUTDOWN_DRAIN_SECONDS = 10.0

# Bounded retry for the one-time journal-mode conversion race at first connect.
_CLEAR_FLAG_MAX_ATTEMPTS = 30
_CLEAR_FLAG_RETRY_SECONDS = 0.1

# 5.3 fixed job intervals (the rest come from Settings).
BACKFILL_COLLECT_INTERVAL_SECONDS = 60
COOKIES_VALIDATE_INTERVAL_SECONDS = 6 * 3600
PROFILE_REFRESH_INTERVAL_SECONDS = 48 * 3600
SELFCHECK_INTERVAL_SECONDS = 24 * 3600

HEARTBEAT_JOB_ID = "heartbeat"
JOB_IDS = (
    "heartbeat",
    "disk-check",
    "network-probe",
    "backfill-collect",
    "cookies-validate",
    "profile-refresh",
    "selfcheck",
)


def _notification_on_event(service: NotificationService) -> Callable[[dict], None]:
    """Adapt the SYNC dict channel of services/ to the notification service.

    The channel stays SYNC (T-BACKFILL-15): jobs fire it inline, never awaited.
    """

    def on_event(payload: dict) -> None:
        event = payload.get("event", "")
        service.emit(event, {key: value for key, value in payload.items() if key != "event"})

    return on_event


@dataclass
class DaemonComponents:
    """Built ONCE at startup (5.1 step 5) and shared by every job (5.3).

    ``engine`` is the DownloadEngine (T-ENGINE-17 naming); it is None when the
    daemon is degraded (no working cookie) -- status/healthcheck/system/cookies
    keep working per 4.1 degraded semantics. Injectable callables follow the
    T-DEPLOY-21 discipline; None resolves to the real default AT CALL TIME.
    """

    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    engine: object | None  # DownloadEngine | None (degraded: no cookie)
    pacer: object  # DownloadPacer protocol (acquire)
    semaphore: object  # async context manager bounding concurrency
    archive: DownloadArchive
    network_monitor: NetworkMonitor
    network_available: asyncio.Event
    notifications: NotificationService
    on_event: Callable | None
    impersonation_fn: Callable | None = None
    which_fn: Callable | None = None
    ytdlp_version_fn: Callable[[], str] | None = None
    ffprobe_fn: Callable | None = None
    sha256_fn: Callable | None = None
    cookie_probe_fn: Callable | None = None
    validate_sleep_fn: Callable | None = None
    disk_usage_fn: Callable | None = None
    # Hot monitor start state (5.3): the CURRENT cycle task, replaced per beat.
    monitor_cycle_task: asyncio.Task | None = None
    # B3 (JD-A-004): hot-start gate clock. In-memory by design: one process,
    # restart resets it and the first beat launches a cycle (T-CLI-6 intact).
    monotonic_fn: Callable[[], float] = time.monotonic
    last_monitor_cycle_started_at: float | None = None
    # 5.6 dedupe-per-edge: the last count seen by the heartbeat.
    last_contention_count: int = 0
    # M5 (6.1): the TikDownBot when TELEGRAM_BOT_TOKEN is set; None otherwise.
    bot: object | None = None


@dataclass
class DaemonRuntime:
    """Handle returned by start_daemon: scheduler + stop event + db engine."""

    components: DaemonComponents
    scheduler: AsyncIOScheduler
    stop_event: asyncio.Event
    db_engine: AsyncEngine


# --- job bodies (5.3): every job re-reads its flags per execution (T-ASYNC-16) ---


async def _monitor_cycle(components: DaemonComponents) -> None:
    """One full monitor cycle as a supervised task (T-DATA-2: real caller)."""
    result = await run_monitor_cycle_once(
        components.session_factory,
        engine=components.engine,
        pacer=components.pacer,
        semaphore=components.semaphore,
        archive=components.archive,
        network_available=components.network_available,
        on_event=components.on_event,  # T-BACKFILL-13
        ffprobe_fn=components.ffprobe_fn,
        sha256_fn=components.sha256_fn,
    )
    logger.info(
        "monitor.cycle: discovered=%d downloaded=%d skipped=%d failed=%d accounts=%d",
        result.get("discovered", 0),
        result.get("downloaded", 0),
        result.get("skipped", 0),
        result.get("failed", 0),
        result.get("accounts", 0),
    )


async def _maybe_start_monitor_cycle(components: DaemonComponents) -> None:
    """Hot monitor start (5.3, T-CLI-6): monitor_running is RE-READ every beat."""
    async with components.session_factory() as session:
        state = await read_status(session)
    if state is None or not state.monitor_running:
        return
    if components.engine is None:
        logger.warning("job.heartbeat: monitor cycle skipped (degraded: no engine)")
        return
    if components.monitor_cycle_task is not None and not components.monitor_cycle_task.done():
        return  # a cycle is already running; coalesce into it
    # B3 (§11.1, T-DEPLOY-9): MONITOR_INTERVAL_MINUTES gates the hot start.
    # Without this guard the cycle relaunched on EVERY 10 s beat, re-listing
    # feeds every ~30 s instead of the configured interval.
    now = components.monotonic_fn()
    interval = components.settings.monitor_interval_minutes * 60
    if (
        components.last_monitor_cycle_started_at is not None
        and now - components.last_monitor_cycle_started_at < interval
    ):
        return
    components.last_monitor_cycle_started_at = now
    components.monitor_cycle_task = create_supervised_task(
        _monitor_cycle(components), name="monitor-cycle"
    )
    logger.info("job.heartbeat: monitor cycle launched (hot start, T-CLI-6)")


def _report_contention_edge(components: DaemonComponents, count: int) -> None:
    """5.6: log daemon.db_contention ONLY crossing the threshold upward (dedupe per edge)."""
    threshold = components.settings.db_busy_timeout_alert_threshold
    if count > threshold and components.last_contention_count <= threshold:
        logger.error("daemon.db_contention: %d 'database is locked' in the last 5 min", count)
        components.notifications.emit(EVENT_DAEMON_DB_CONTENTION, {"count": count})
    components.last_contention_count = count


async def _heartbeat_job(components: DaemonComponents) -> None:
    """10 s beat (5.3): heartbeat + contention window + hot monitor start.

    Trace (T-DATA-2): last_heartbeat_at + db_busy_count_5min columns. The
    monitor_running flag is read EVERY execution, never cached (T-ASYNC-16).
    """
    busy_count = db_busy_count_5min()
    await retry_on_locked(lambda: _write_heartbeat(components, busy_count))
    _report_contention_edge(components, busy_count)
    await _maybe_start_monitor_cycle(components)


async def _write_heartbeat(components: DaemonComponents, busy_count: int) -> None:
    async with components.session_factory() as session:
        await write_heartbeat(session, pid=os.getpid(), db_busy_count=busy_count)


async def _disk_check_job(components: DaemonComponents) -> None:
    """900 s disk check (8.2). Trace: downloads_paused column + structured log."""
    paused = await check_disk_pause(
        components.settings,
        components.session_factory,
        on_event=components.on_event,
        disk_usage_fn=components.disk_usage_fn,
    )
    logger.info("job.disk-check: downloads_paused=%d", int(paused))


async def _network_probe_job(components: DaemonComponents) -> None:
    """30 s network probe (8.1). Trace: network state + structured log.

    ponytail: no extra next_allowed_at gate -- the 30 s interval IS the
    healthy cadence (8.1); the failure backoff lives in the monitor's state
    machine (offline threshold), not in probe frequency.
    """
    online = await components.network_monitor.probe_once()
    logger.info("job.network-probe: online=%d", int(online))


async def _account_id_for(session_factory, username: str) -> int | None:
    async with session_factory() as session:
        return (
            (
                await session.execute(
                    select(MonitoredAccount.id).where(MonitoredAccount.username == username)
                )
            )
            .scalars()
            .first()
        )


async def _run_collected_backfill(
    components: DaemonComponents, account_id: int, username: str
) -> None:
    """Supervised run_backfill launch (9.1, T-BACKFILL-13); slot/no-cookie skips log."""
    try:
        status = await run_backfill(
            components.session_factory,
            account_id,
            engine=components.engine,
            pacer=components.pacer,
            semaphore=components.semaphore,
            archive=components.archive,
            on_event=components.on_event,  # T-BACKFILL-13
            network_online_fn=lambda: components.network_available.is_set(),
        )
        logger.info("job.backfill-collect: %s finished (%s)", username, status)
    except ConfigurationError as exc:
        # slot_busy / no_cookies: the account stays untouched, the trace logs it.
        logger.warning("job.backfill-collect: skipped %s: %s", username, exc)


async def _backfill_collect_job(components: DaemonComponents) -> None:
    """60 s collector (5.3): queued + resumable paused accounts -> supervised runs.

    Trace (T-DATA-2): 'job.backfill-collect' structured line with collected/
    launched counts; per-account outcomes log through _run_collected_backfill.
    """
    collected = await collect_queued_backfills(
        components.session_factory,
        engine_factory_fn=lambda account: None,  # unused by the real runner
        network_online_fn=lambda: components.network_available.is_set(),
    )
    launched = 0
    for username in collected:
        if components.engine is None:
            logger.warning("job.backfill-collect: degraded (no engine); not launching %s", username)
            continue
        account_id = await _account_id_for(components.session_factory, username)
        if account_id is None:
            continue
        create_supervised_task(
            _run_collected_backfill(components, account_id, username),
            name=f"backfill:{username}",
        )
        launched += 1
    logger.info("job.backfill-collect: collected=%d launched=%d", len(collected), launched)


def _default_cookie_probe(blob: bytes, url: str, max_entries: int) -> bool:
    """Composition root for the M2 probe (7): the service gets a bool probe_fn."""
    return entries_have_video(probe_profile(blob, url, max_entries))


async def _cookies_validate_job(components: DaemonComponents) -> None:
    """6 h sequential cookie validation (7). Trace: cookie.validated events + log."""
    settings = components.settings
    if not settings.cookie_validation_url:
        logger.warning("job.cookies-validate: skipped (COOKIE_VALIDATION_URL not configured)")
        return
    if components.engine is None:
        logger.warning("job.cookies-validate: skipped (degraded: no working cookie to probe)")
        return
    counts = await validate_all_cookies(
        components.session_factory,
        probe_urls=settings.cookie_validation_url,
        probe_fn=components.cookie_probe_fn or _default_cookie_probe,
        max_entries=settings.cookie_probe_max_entries,
        on_event=components.on_event,
        sleep_fn=components.validate_sleep_fn,
    )
    logger.info(
        "job.cookies-validate: valid=%d invalid=%d inconclusive=%d",
        counts["valid"],
        counts["invalid"],
        counts["inconclusive"],
    )


async def _profile_refresh_job(components: DaemonComponents) -> None:
    """48 h profile refresh (5.3, 3.1). Trace: profile.refreshed events + log."""
    if components.engine is None:
        logger.warning("job.profile-refresh: skipped (degraded: no engine)")
        return
    refreshed = await refresh_all_profiles(
        components.session_factory,
        engine=components.engine,
        on_event=components.on_event,
    )
    logger.info("job.profile-refresh: refreshed=%d", refreshed)


async def _selfcheck_job(components: DaemonComponents) -> None:
    """24 h selfcheck (5.3) + T-ENGINE-28 last-known-good version discipline."""
    result = await run_selfcheck(
        components.settings,
        components.session_factory,
        impersonation_fn=components.impersonation_fn,
        ffprobe_path_fn=components.which_fn,
        ffmpeg_path_fn=components.which_fn,
    )
    version = components.ytdlp_version_fn()
    if result.ok:
        async with components.session_factory() as session:
            await record_last_known_good_ytdlp(session, version)
        components.notifications.emit(EVENT_SELFCHECK_OK, {})
        logger.info("job.selfcheck: ok (yt_dlp %s)", version)
        return
    async with components.session_factory() as session:
        state = await read_status(session)
    last_good = state.last_known_good_ytdlp_version if state is not None else None
    if last_good is not None and version != last_good:
        # T-ENGINE-28: a failure with a version DIFFERENT from the last good
        # one defaults to an update regression, never a TikTok diagnosis.
        logger.error(
            "selfcheck.regression: yt_dlp %s failed while last known good was %s; "
            "default diagnosis: update regression (T-ENGINE-28)",
            version,
            last_good,
        )
    components.notifications.emit(
        EVENT_SELFCHECK_FAILED, {"reason": result.degraded_reason or "unknown"}
    )
    logger.warning("job.selfcheck: failed (%s)", result.degraded_reason)


async def _static_site_job(components: DaemonComponents) -> None:
    """Optional 10.3 dashboard render (only registered when STATIC_SITE_ENABLED).

    command_reference=None: the scheduler has no typer tree, so the rendered
    docs column shows the service's placeholder (10.3 admission rule: never
    import cli/ from the daemon).
    """
    out_dir = resolve_output_dir(components.settings)
    paths = await render_site(
        components.session_factory, components.settings, out_dir, command_reference=None
    )
    logger.info("job.static-site: wrote %d files to %s", len(paths), out_dir)


# --- stop watcher (T-CLI-6) ---


async def _watch_stop_requested(
    session_factory: async_sessionmaker[AsyncSession], stop_event: asyncio.Event
) -> None:
    """Poll stop_requested every 0.5 s so `daemon stop` is honored (T-CLI-6).

    Writing the flag is not enough: this watcher is the reader that gives the
    flag its effect. Re-read on every poll, never cached (T-ASYNC-16 spirit).
    """
    while True:
        await asyncio.sleep(STOP_POLL_INTERVAL_SECONDS)
        await retry_on_locked(lambda: _check_stop(session_factory, stop_event))


async def _check_stop(session_factory, stop_event: asyncio.Event) -> None:
    async with session_factory() as session:
        if await read_stop_requested(session):
            logger.info("daemon.stop_requested detected via watcher")
            stop_event.set()
            return


async def _clear_inherited_stop_flag(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """5.1 step 2 (T-CLI-7): clear a stop_requested inherited from a previous run.

    On a fresh database the table does not exist yet (migrations run next);
    that is not an inheritable flag, so the missing table is ignored.
    """

    async def _clear() -> None:
        async with session_factory() as session:
            await clear_stop_requested(session)

    try:
        await retry_on_locked(
            _clear,
            attempts=_CLEAR_FLAG_MAX_ATTEMPTS,
            base_delay=_CLEAR_FLAG_RETRY_SECONDS,
        )
    except OperationalError as exc:
        if "no such table" in str(exc):
            logger.info("daemon_state table absent (fresh database): nothing to clear")
            return
        raise


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    """POSIX: SIGINT/SIGTERM -> stop_event. Windows: guarded (see module docstring)."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        if not hasattr(loop, "add_signal_handler"):
            break
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows event loop: SIGINT arrives as KeyboardInterrupt instead.
            break


def _build_scheduler(components: DaemonComponents) -> AsyncIOScheduler:
    """All 7 jobs of 5.3: in-memory jobstore, max_instances=1 + coalesce=True (T-ASYNC-13).

    Every job gets its components bundle; every flag a job reads is re-read per
    execution (T-ASYNC-16). The heartbeat fires immediately; the other jobs
    start after their first interval (no startup stampede).
    """
    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    settings = components.settings
    specs = (
        (HEARTBEAT_JOB_ID, _heartbeat_job, settings.heartbeat_interval_seconds),
        ("disk-check", _disk_check_job, settings.disk_check_interval_seconds),
        ("network-probe", _network_probe_job, settings.network_probe_interval_seconds),
        ("backfill-collect", _backfill_collect_job, BACKFILL_COLLECT_INTERVAL_SECONDS),
        ("cookies-validate", _cookies_validate_job, COOKIES_VALIDATE_INTERVAL_SECONDS),
        ("profile-refresh", _profile_refresh_job, PROFILE_REFRESH_INTERVAL_SECONDS),
        ("selfcheck", _selfcheck_job, SELFCHECK_INTERVAL_SECONDS),
    )
    for job_id, job_fn, seconds in specs:
        scheduler.add_job(
            job_fn,
            trigger="interval",
            seconds=seconds,
            args=(components,),
            id=job_id,
            max_instances=1,  # T-ASYNC-13: a slow job must never overlap itself
            coalesce=True,
        )
    # 10.3: OPTIONAL 8th job, registered ONLY when STATIC_SITE_ENABLED=true
    # (the same conditional-flag pattern as the bot above: absent by default,
    # so the 7-job registration contract of 5.3 is unchanged).
    if settings.static_site_enabled:
        scheduler.add_job(
            _static_site_job,
            trigger="interval",
            minutes=settings.static_site_interval_minutes,
            args=(components,),
            id="static-site",
            max_instances=1,
            coalesce=True,
        )
        # NOTE: no immediate first beat. An immediate heartbeat racing loop
        # teardown cancels a connection mid-PRAGMA, and aiosqlite's shielded
        # terminate then wedges the loop close on Windows (root-caused M4).
        # Healthcheck tolerance is 3x interval and the container start-period
        # is 60s, so a 10s first beat is safe; tests get a quiet teardown.
    return scheduler


async def start_daemon(
    settings: Settings,
    *,
    notifications: NotificationService | None = None,
    impersonation_fn: Callable | None = None,
    which_fn: Callable | None = None,
    ytdlp_version_fn: Callable[[], str] | None = None,
    ffprobe_fn: Callable | None = None,
    sha256_fn: Callable | None = None,
    cookie_probe_fn: Callable | None = None,
    validate_sleep_fn: Callable | None = None,
    disk_usage_fn: Callable | None = None,
    get_cookie_fn: Callable | None = None,
    stop_event: asyncio.Event | None = None,
) -> DaemonRuntime:
    """Run the 5.1 startup order and return the live runtime handle.

    Awaitable and inspectable so tests can drive startup directly; the CLI
    entry point stays run_daemon_lifecycle. Injectable callables are test
    seams (T-DEPLOY-21); None resolves to the real default at call time.
    """
    # (1) T-DEPLOY-8: fail fast on invalid configuration before anything else.
    settings.validate_for_daemon()

    db_engine = create_db_engine(sqlite_url_for(settings.data_dir))
    session_factory = make_session_factory(db_engine)
    stop_event = stop_event if stop_event is not None else asyncio.Event()
    try:
        # (2) T-CLI-7: an inherited flag would auto-shutdown this startup.
        await _clear_inherited_stop_flag(session_factory)
        # (3) T-ASYNC-4: migrations never run on the loop thread.
        await asyncio.to_thread(run_migrations, settings.data_dir)
        # (4) T-DEPLOY-6: reapply logging after Alembic's fileConfig stomps it.
        setup_logging(settings, force=True)
        # (5) Components BEFORE anything consults them (5.1 step 5).
        get_cookie = get_cookie_fn or get_working_cookie
        cookie = await get_cookie(session_factory)
        engine = YtDlpEngine(cookie.cookie_blob, settings) if cookie is not None else None
        archive = DownloadArchive(settings.data_dir / "download_archive.txt", session_factory)
        pacer = DownloadPacer(session_factory, settings)
        semaphore = DownloadSemaphore(settings.max_concurrent_downloads)
        network_available = asyncio.Event()
        network_available.set()  # T-ENGINE-7: created PRE-SET
        network_monitor = NetworkMonitor(settings, network_available)
        notifications = notifications if notifications is not None else NoopNotificationService()
        components = DaemonComponents(
            settings=settings,
            session_factory=session_factory,
            engine=engine,
            pacer=pacer,
            semaphore=semaphore,
            archive=archive,
            network_monitor=network_monitor,
            network_available=network_available,
            notifications=notifications,
            on_event=_notification_on_event(notifications),
            impersonation_fn=impersonation_fn,
            which_fn=which_fn,
            ytdlp_version_fn=ytdlp_version_fn or _ytdlp_version,
            ffprobe_fn=ffprobe_fn,
            sha256_fn=sha256_fn,
            cookie_probe_fn=cookie_probe_fn,
            validate_sleep_fn=validate_sleep_fn,
            disk_usage_fn=disk_usage_fn,
        )
        # (6) Initial state + startup reconciliations (5.1 step 6).
        await retry_on_locked(lambda: _register_start(session_factory))
        if settings.monitor_autostart:
            await set_monitor_running(session_factory, True)
            logger.info("daemon.monitor_autostart: monitor_running=1")
        transitions = await reconcile_pending_monitor_transitions(session_factory)
        stale = await reconcile_stale_backfills(session_factory)
        logger.info(
            "daemon.reconcile: monitor_transitions=%d stale_backfills_requeued=%d",
            transitions,
            stale,
        )
        # (7) Startup probes: degraded_reason ONLY (5.1 step 7, T-DEPLOY-10).
        probes = probe_startup(
            impersonation_fn=impersonation_fn,
            ffmpeg_path_fn=which_fn,
            ffprobe_path_fn=which_fn,
        )
        await retry_on_locked(
            lambda: _record_probes(session_factory, probes, cookies_ok=engine is not None)
        )
        # (8) Telegram bot (6.1): started BEFORE the scheduler (5.1 order).
        # STRICTLY an enhancement: any failure here only logs -- the daemon
        # continues WITHOUT it (the bot is last-before-scheduler for a reason).
        # T-BOT-3: the bot gets the daemon's OWN session_factory injected, so
        # there is exactly ONE engine (built in step 5, disposed by
        # shutdown_daemon); the bot never creates or disposes an engine itself.
        if settings.telegram_bot_token:
            try:
                components.bot = await _start_bot(components)
                logger.info("daemon.bot: polling started (supervised)")
            except Exception:
                logger.exception("daemon.bot: startup failed; daemon continues WITHOUT bot")
        # (9) ALL 7 jobs of 5.3 (T-ASYNC-13, T-DATA-2).
        # Deterministic liveness signal FIRST: one heartbeat written directly
        # in startup, before any scheduler job exists. Tests and healthcheck
        # see a fresh beat immediately; the scheduler takes over one full
        # interval later (no immediate beat racing loop teardown, root-caused
        # in the M4 wedge investigation).
        await retry_on_locked(lambda: _write_heartbeat(components, 0))
        scheduler = _build_scheduler(components)
        scheduler.start()
        create_supervised_task(
            _watch_stop_requested(session_factory, stop_event), name="stop-watcher"
        )
        notifications.emit(
            EVENT_DAEMON_STARTED,
            {"pid": os.getpid(), "data_dir": str(settings.data_dir)},
        )
        logger.info("daemon started (pid %s, data_dir %s)", os.getpid(), settings.data_dir)
        return DaemonRuntime(
            components=components, scheduler=scheduler, stop_event=stop_event, db_engine=db_engine
        )
    except BaseException:
        await db_engine.dispose()
        raise


async def _start_bot(components: DaemonComponents) -> TikDownBot:
    """Build and start the bot (6.1): injected deps, strict sequence, supervised loop.

    T-BOT-1: initialize -> start -> updater.start_polling(25); NEVER
    run_polling on the daemon's live loop. The §6.5 supervision loop runs as a
    supervised task (5.5) so shutdown drains it together with the bot stop.
    """
    bot = TikDownBot(components.settings, components.session_factory)
    bot.register_handlers()
    await start_bot_polling(bot.application)
    supervisor = PollingSupervisor(
        bot.application,
        interval=components.settings.polling_healthcheck_interval,
        max_failures=components.settings.polling_healthcheck_max_failures,
    )
    create_supervised_task(supervisor.run(), name="bot-polling-supervision")
    return bot


async def shutdown_daemon(runtime: DaemonRuntime) -> None:
    """5.2 shutdown order: scheduling off, daemon.stopped BEFORE the drain."""
    runtime.scheduler.shutdown(wait=False)
    # T-ASYNC-6: emit BEFORE the drain so shutdown observers see it even when
    # the drain cancels work in flight.
    runtime.components.notifications.emit(EVENT_DAEMON_STOPPED, {"reason": "shutdown"})
    await drain_supervised_tasks(SHUTDOWN_DRAIN_SECONDS)
    if runtime.components.bot is not None:
        # T-BOT-1: strict stop sequence, per-stage tolerant (6.1).
        await stop_bot_polling(runtime.components.bot.application)
    # ponytail: scheduler-job tasks are NOT in the supervised registry and a
    # task wedged in aiosqlite's shielded terminate ignores cancellation, so
    # a catch-all cancel/wait here is worse than useless (it also cancels the
    # CALLER's task). The common trigger was removed at the root: the first
    # heartbeat is written synchronously in startup, and jobs first fire a
    # full interval later, so shutdown no longer races an in-flight DB write.
    # Residual stop-window race: see M4 ledger.
    await retry_on_locked(lambda: _clear_runtime(runtime.components.session_factory))
    await runtime.db_engine.dispose()
    logger.info("daemon stopped cleanly")


async def run_daemon_lifecycle(settings: Settings, **injections) -> None:
    """Run the whole daemon lifecycle under ONE event loop (T-ASYNC-3, 5.1/5.2).

    The caller must drive this with exactly one asyncio.run() in the CLI layer.
    ``injections`` pass through to start_daemon (test seams, T-DEPLOY-21).
    """
    runtime = await start_daemon(settings, **injections)
    _install_signal_handlers(runtime.stop_event)
    try:
        await runtime.stop_event.wait()
    finally:
        await shutdown_daemon(runtime)


# --- small local helpers ---


def _ytdlp_version() -> str:
    """T-ENGINE-20: compare against yt_dlp.version.__version__, never PyPI."""
    return yt_dlp.version.__version__


async def _register_start(session_factory) -> None:
    async with session_factory() as session:
        await register_daemon_start(session, pid=os.getpid())


async def _record_probes(session_factory, probes, *, cookies_ok: bool) -> None:
    async with session_factory() as session:
        await record_startup_probes(
            session,
            ffmpeg_ok=probes.ffmpeg_ok,
            ffprobe_ok=probes.ffprobe_ok,
            impersonation_reason=probes.impersonation_reason,
            cookies_ok=cookies_ok,
        )


async def _clear_runtime(session_factory) -> None:
    async with session_factory() as session:
        await clear_daemon_runtime(session)

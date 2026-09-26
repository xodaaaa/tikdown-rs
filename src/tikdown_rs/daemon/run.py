"""Foreground daemon lifecycle: strict startup order (5.1), bounded shutdown (5.2).

Trampas neutralizadas: T-ASYNC-3, T-ASYNC-4, T-CLI-6, T-CLI-7, T-DEPLOY-6,
T-DEPLOY-8, T-ASYNC-13. Regla: 5.1, 5.2, 5.3, 5.5, 11.2.

Signal coverage: on POSIX, SIGINT and SIGTERM are installed with
``loop.add_signal_handler`` and route into the same shutdown path (stop_event).
On Windows, ``add_signal_handler`` is not supported by the event loop: SIGINT
arrives natively as KeyboardInterrupt, which asyncio.run converts into a
cancellation of the lifecycle coroutine, so the same finally-block shutdown path
runs; SIGTERM has no asyncio handler on Windows and is out of scope for M0
(plan 14.3 targets Docker/Linux, where SIGTERM is the stop signal).

M0 startup order (STRICT, 5.1):
1. validate_for_daemon (T-DEPLOY-8).
2. clear inherited stop_requested (T-CLI-7) - before anything else reads it.
3. run_migrations via asyncio.to_thread (T-ASYNC-4: Alembic's env.py calls
   asyncio.run internally, so it must never run on the loop thread).
4. setup_logging(force=True) immediately after migrations (T-DEPLOY-6: Alembic's
   fileConfig stomps the root logger).
5. engine + session factory are already open (created before step 2 to clear the
   flag pre-migrations; Alembic uses its own engine from the URL).
6. ensure singleton row, write daemon_pid + daemon_started_at, monitor_running=0.
7. heartbeat job only (AsyncIOScheduler, in-memory jobstore, max_instances=1 +
   coalesce=True, T-ASYNC-13) and the stop_requested watcher task.
8. await stop_event.

Shutdown (5.2): stop_event by signal or watcher (poll <= 0.5 s); scheduler
shutdown stops the *scheduling* (T-ASYNC-9: it does not drain jobs); bounded
drain (10 s) of supervised tasks; daemon_state cleanup; engine dispose.
"""

import asyncio
import logging
import os
import signal
from datetime import UTC, datetime

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.config import Settings
from tikdown_rs.core.daemon_state import (
    clear_daemon_runtime,
    clear_stop_requested,
    read_stop_requested,
    register_daemon_start,
    write_heartbeat,
)
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.logging import setup_logging
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.core.tasks import create_supervised_task, drain_supervised_tasks

logger = logging.getLogger(__name__)

# 11.2 constants: stop_requested polling <= 0.5 s; shutdown drain 10 s.
STOP_POLL_INTERVAL_SECONDS = 0.5
SHUTDOWN_DRAIN_SECONDS = 10.0

# Bounded retry for the one-time journal-mode conversion race at first connect.
_CLEAR_FLAG_MAX_ATTEMPTS = 30
_CLEAR_FLAG_RETRY_SECONDS = 0.1

HEARTBEAT_JOB_ID = "heartbeat"


async def _heartbeat(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """One heartbeat beat: persist last_heartbeat_at + pid bookkeeping (5.3)."""
    async with session_factory() as session:
        await write_heartbeat(session, pid=os.getpid())


async def _watch_stop_requested(
    session_factory: async_sessionmaker[AsyncSession], stop_event: asyncio.Event
) -> None:
    """Poll stop_requested every 0.5 s so `daemon stop` is honored (T-CLI-6).

    Writing the flag is not enough: this watcher is the reader that gives the
    flag its effect. Re-read on every poll, never cached (T-ASYNC-16 spirit).
    """
    while True:
        await asyncio.sleep(STOP_POLL_INTERVAL_SECONDS)
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
    for attempt in range(_CLEAR_FLAG_MAX_ATTEMPTS):
        try:
            async with session_factory() as session:
                await clear_stop_requested(session)
            return
        except OperationalError as exc:
            message = str(exc)
            if "no such table" in message:
                logger.info("daemon_state table absent (fresh database): nothing to clear")
                return
            # A concurrent opener can win the one-time delete->WAL journal
            # conversion, which takes an exclusive lock and bypasses
            # busy_timeout; a bounded retry rides out that window.
            if "database is locked" in message and attempt < _CLEAR_FLAG_MAX_ATTEMPTS - 1:
                logger.warning("daemon_state locked while clearing stop_requested; retrying")
                await asyncio.sleep(_CLEAR_FLAG_RETRY_SECONDS)
                continue
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


def _build_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    heartbeat_interval_seconds: int,
) -> AsyncIOScheduler:
    """Heartbeat-only scheduler for M0 (5.3): in-memory jobstore, no persistence."""
    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    scheduler.add_job(
        _heartbeat,
        trigger="interval",
        seconds=heartbeat_interval_seconds,
        args=(session_factory,),
        id=HEARTBEAT_JOB_ID,
        max_instances=1,  # T-ASYNC-13: a slow job must never overlap itself
        coalesce=True,
        next_run_time=datetime.now(UTC),  # first beat immediately
    )
    return scheduler


async def run_daemon_lifecycle(settings: Settings) -> None:
    """Run the whole daemon lifecycle under ONE event loop (T-ASYNC-3, 5.1/5.2).

    The caller must drive this with exactly one asyncio.run() in the CLI layer.
    """
    # (1) T-DEPLOY-8: fail fast on invalid configuration before anything else.
    settings.validate_for_daemon()

    engine = create_db_engine(sqlite_url_for(settings.data_dir))
    session_factory = make_session_factory(engine)
    stop_event = asyncio.Event()
    scheduler: AsyncIOScheduler | None = None
    try:
        # (2) T-CLI-7: an inherited flag would auto-shutdown this startup.
        await _clear_inherited_stop_flag(session_factory)
        # (3) T-ASYNC-4: migrations never run on the loop thread.
        await asyncio.to_thread(run_migrations, settings.data_dir)
        # (4) T-DEPLOY-6: reapply logging after Alembic's fileConfig stomps it.
        setup_logging(settings, force=True)
        # (6) M0 initial state: monitor starts stopped (monitor_running=0).
        async with session_factory() as session:
            await register_daemon_start(session, pid=os.getpid())
        # (7) M0 registers ONLY the heartbeat job + the stop watcher.
        scheduler = _build_scheduler(session_factory, settings.heartbeat_interval_seconds)
        scheduler.start()
        create_supervised_task(
            _watch_stop_requested(session_factory, stop_event), name="stop-watcher"
        )
        _install_signal_handlers(stop_event)
        logger.info("daemon started (pid %s, data_dir %s)", os.getpid(), settings.data_dir)
        # (8)
        await stop_event.wait()
    finally:
        # 5.2 shutdown order: scheduling off first (T-ASYNC-9), then the real
        # drain through the supervised-task registry, then state cleanup.
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        # ponytail: the in-flight heartbeat job is not in the supervised
        # registry; the drain's awaits let its single upsert finish before the
        # cleanup write. If a heartbeat job ever grows, register it too.
        await drain_supervised_tasks(SHUTDOWN_DRAIN_SECONDS)
        async with session_factory() as session:
            await clear_daemon_runtime(session)
        await engine.dispose()
        logger.info("daemon stopped cleanly")

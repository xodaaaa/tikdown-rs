"""Runner del daemon — daemon/run.py (§5.1, §5.2).

TODO el ciclo de vida (start + run + shutdown) corre dentro de UN único
`asyncio.run(_lifecycle())` (L-B1 — crítico). Fail-fast (T25/T6), migraciones
con re-logging (T72/L-B3), jobs como tareas supervisadas (T27/T28), drenaje
por registro (T9), bot manual (T10), monitor siempre detenido (T5.1).

story: e02s03
"""

from __future__ import annotations

import asyncio
import logging
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from tikdown_rs.core.config import Settings
from tikdown_rs.core.daemon_state import update_heartbeat
from tikdown_rs.core.db import create_async_engine_wal
from tikdown_rs.core.logging import setup_logging
from tikdown_rs.core.migrations import apply_migrations
from tikdown_rs.core.tasks import cancel_pending_tasks, create_supervised_task
from tikdown_rs.core.verify import selfcheck_ffmpeg, selfcheck_impersonation

LOG = logging.getLogger("tikdown_rs.daemon")


class DaemonRunner:
    """Orquesta el ciclo de vida del daemon en un único event loop."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.scheduler = AsyncIOScheduler()
        self._stop_event = asyncio.Event()
        self._bot = None
        self._engine = None

    # --- Ciclo de vida (L-B1: un solo asyncio.run) ---

    async def _start(self) -> None:
        """Arranque fail-fast (§5.1)."""
        # 1. Fail-fast de configuración (T25)
        self.settings.validate_for_daemon()

        # 2. Selfcheck de impersonación (T6) + ffmpeg (T46) — aborta si falla
        selfcheck_impersonation()
        selfcheck_ffmpeg()

        # 3. Migraciones idempotentes (T29/T68) en thread (L-B3: alembic usa asyncio.run)
        db_url = f"sqlite+aiosqlite:///{self.settings.data_dir / 'tikdown-rs.db'}"
        await asyncio.to_thread(apply_migrations, db_url)

        # 4. REAPLICAR logging tras migrar (T72: fileConfig pisa el root logger)
        setup_logging(self.settings.log_level, json_output=True)

        # 5. Engine + scheduler
        self._engine = create_async_engine_wal(db_url)
        self.scheduler.start()

        # 6. Monitor SIEMPRE detenido (T5.1); la reconciliación de estado se hará
        #    en jobs (heartbeat aplica monitor_running en caliente).

        LOG.info("daemon.started")

    async def _run(self) -> None:
        """Bucle principal: watcher de stop_requested (L-B1)."""
        # Registrar jobs de intervalo como tareas supervisadas (T27)
        self._register_jobs()

        # Bot en el mismo loop (T10) si está habilitado
        if self.settings.telegram_bot_token:
            await self._start_bot()

        # Watcher: espera stop_requested o señal (L-B1)
        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)

    async def _shutdown(self) -> None:
        """Apagado limpio (§5.2): drena el registro, no el scheduler (T9/T28)."""
        LOG.info("daemon.shutting_down")

        # Parar el bot (T10): updater.stop → stop → shutdown
        if self._bot is not None:
            await self._stop_bot()

        # Drenar tareas supervisadas (T28) — el scheduler cancela en vez de esperar (T9)
        await cancel_pending_tasks(timeout=10.0)

        # Shutdown del scheduler (señal, no drenaje)
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:  # pragma: no cover
            LOG.warning("daemon.scheduler_shutdown_error", exc_info=True)

        if self._engine is not None:
            await self._engine.dispose()

        LOG.info("daemon.stopped")

    async def _lifecycle(self) -> None:
        """UN único ciclo: start → run → shutdown (L-B1)."""
        await self._start()
        try:
            await self._run()
        finally:
            await self._shutdown()

    # --- Jobs ---

    def _register_jobs(self) -> None:
        """Registra jobs de intervalo que lanzan tareas supervisadas (T27)."""
        hb_seconds = self.settings.heartbeat_interval_seconds

        async def _heartbeat_job() -> None:
            # Heartbeat persistido (T37: helper con commit interno)
            engine = self._engine
            if engine is None:
                return
            from sqlalchemy.ext.asyncio import async_sessionmaker

            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as s:
                await update_heartbeat(s)

        # Heartbeat como tarea supervisada (T27/T28)
        def _schedule_heartbeat() -> None:
            create_supervised_task(_heartbeat_job(), name="heartbeat")

        self.scheduler.add_job(
            _schedule_heartbeat,
            "interval",
            seconds=hb_seconds,
            id="heartbeat",
            max_instances=1,
        )

        # e13s01: recogida automática de backfills 'queued' + 'paused' reanudables
        async def _backfill_collect_job() -> None:
            engine = self._engine
            if engine is None:
                return
            from sqlalchemy.ext.asyncio import async_sessionmaker

            from tikdown_rs.core.tasks import create_supervised_task
            from tikdown_rs.services import backfill as backfill_svc
            from tikdown_rs.services.cookies import working_cookies_list

            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as s:
                cookie = await working_cookies_list(s)

                # El job lanza la recogida como tarea supervisada (T27) — el
                # estado de red del daemon se consulta vía el monitor (default online)
                async def _run() -> None:
                    async with maker() as s2:
                        await backfill_svc.collect_queued_backfills(
                            s2,
                            engine=engine,
                            cookies=cookie,
                            owner="daemon",
                        )

                create_supervised_task(_run(), name="backfill-collect")

        def _schedule_backfill() -> None:
            create_supervised_task(_backfill_collect_job(), name="backfill-collect-job")

        self.scheduler.add_job(
            _schedule_backfill,
            "interval",
            seconds=60,
            id="backfill-collect",
            max_instances=1,  # T44: no solapar el ciclo
        )

    # --- Bot (T10) ---

    async def _start_bot(self) -> None:
        """Arranca el bot con supervisión (e12s01) en el mismo loop (T10).

        Usa TelegramBot (que registra handlers + healthcheck del polling)
        en vez de un Application propio sin supervisión (T71/§6.1).
        """
        from tikdown_rs.daemon.telegram.bot import TelegramBot

        bot = TelegramBot(
            settings=self.settings,
            engine=self._engine,
            on_event=None,
            owns_engine=False,  # el daemon gestiona el engine
        )
        await bot.start()
        self._bot = bot
        LOG.info("daemon.bot_started")

    async def _stop_bot(self) -> None:
        """Apagado del bot (T10): updater.stop → stop → shutdown."""
        bot = self._bot
        if bot is None:
            return
        await bot.stop()
        self._bot = None


def run_daemon(settings: Settings) -> None:
    """Entrypoint: TODO el ciclo en UN único asyncio.run (L-B1)."""
    runner = DaemonRunner(settings)

    def _request_stop(signum=None, frame=None) -> None:  # noqa: ANN001
        LOG.info("daemon.signal_received", extra={"signum": signum})
        runner._stop_event.set()

    # Watcher de señales (SIGTERM/SIGINT) → stop_event (L-B1)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)
    except (ValueError, OSError):  # pragma: no cover - no main thread
        pass
    asyncio.run(runner._lifecycle())

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
from tikdown_rs.core.daemon_state import set_stop_requested, update_heartbeat
from tikdown_rs.core.db import create_async_engine_wal
from tikdown_rs.core.logging import setup_logging
from tikdown_rs.core.migrations import apply_migrations
from tikdown_rs.core.tasks import cancel_pending_tasks, create_supervised_task
from tikdown_rs.core.verify import selfcheck_ffmpeg, selfcheck_impersonation

LOG = logging.getLogger("tikdown_rs.daemon")

# Sondeo de stop_requested (bug #21): mitad del intervalo de heartbeat,
# acotado a [0.5, 5]s para que la latencia de apagado nunca supere ~5s.
STOP_POLL_SECONDS = 0.5
STOP_POLL_MAX_SECONDS = 5.0


class DaemonRunner:
    """Orquesta el ciclo de vida del daemon en un único event loop."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.scheduler = AsyncIOScheduler()
        self._stop_event = asyncio.Event()
        self._bot = None
        self._engine = None
        self._network_monitor = None  # 2.1-r2: probe de red (§9)
        # 2.1-r2: job del monitor de cuentas (comandado por monitor_running).
        # Cookies working se resuelven en _run() y se inyectan al job.
        from tikdown_rs.daemon.monitor_job import MonitorJob

        self._monitor_job = MonitorJob(maker=None, settings=settings)

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
        #    — respeta la config de archivo rotado (e14s01) desde Settings
        setup_logging(
            self.settings.log_level,
            json_output=True,
            log_file_path=self.settings.log_file_path,
            log_file_max_bytes=self.settings.log_file_max_bytes,
            log_file_backup_count=self.settings.log_file_backup_count,
            log_file_when=self.settings.log_file_when,
        )

        # 5. Engine + scheduler
        self._engine = create_async_engine_wal(db_url)
        self.scheduler.start()

        # 6. Monitor SIEMPRE detenido (T5.1); la reconciliación de estado se hará
        #    en jobs (heartbeat aplica monitor_running en caliente).

        LOG.info("daemon.started")

    async def _check_stop_requested(self) -> None:
        """Lee stop_requested de daemon_state y consume el flag (bug #21).

        `daemon stop` (CLI) escribe stop_requested=True vía SQLite; el daemon
        no recibe señal alguna, así que debe sondear la DB. Consumir el flag
        (reset a False) evita que el próximo arranque se auto-apague en seco.
        """
        if self._engine is None:
            return
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from tikdown_rs.core.daemon_state import get_or_create_daemon_state

        maker = async_sessionmaker(self._engine, expire_on_commit=False)
        async with maker() as s:
            row = await get_or_create_daemon_state(s)
            if row.stop_requested:
                await set_stop_requested(s, False)  # consumir (commit interno)
                LOG.info("daemon.stop_requested_detected")
                self._stop_event.set()

    async def _resolve_cookies_blob(self) -> bytes | None:
        """Blob descifrado de la primera cookie working (F-01/F-15) o None."""
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from tikdown_rs.core.crypto import decrypt_cookie, load_or_create_fernet_key
        from tikdown_rs.services.cookies import working_cookies_list

        maker = async_sessionmaker(self._engine, expire_on_commit=False)
        async with maker() as s:
            cookie = await working_cookies_list(s)
        if not cookie:
            return None
        key = load_or_create_fernet_key(self.settings.data_dir / "fernet.key")
        return decrypt_cookie(cookie[0].encrypted_blob, key)

    async def _run(self) -> None:
        """Bucle principal: watcher de stop_requested (L-B1, bug #21)."""
        # 2.1-r3: cookies working para el motor del monitor (antes de los jobs)
        if self._engine is not None:
            self._monitor_job._cookies_blob = await self._resolve_cookies_blob()
        # Registrar jobs de intervalo como tareas supervisadas (T27)
        self._register_jobs()

        # Bot en el mismo loop (T10) si está habilitado
        if self.settings.telegram_bot_token:
            await self._start_bot()

        # Watcher: espera señal (stop_event) o sondea stop_requested en DB
        # cada ~0.5s (bug #21) — `daemon stop` no envía señales.
        poll = min(STOP_POLL_SECONDS, self.settings.heartbeat_interval_seconds / 2)
        poll = max(min(poll, STOP_POLL_MAX_SECONDS), STOP_POLL_SECONDS)
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=poll)
            except TimeoutError:
                await self._check_stop_requested()

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
        # 2.1-r2: inyectar el maker del engine al job del monitor. Las cookies
        # working las resuelve _run() en caliente (DB lista, antes de los jobs).
        if self._engine is not None:
            from sqlalchemy.ext.asyncio import async_sessionmaker

            self._monitor_job._maker = async_sessionmaker(self._engine, expire_on_commit=False)
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
            # e03s02/2.1-r2: el heartbeat APLICA monitor_running en caliente
            # (lo que cli/monitor.py siempre prometió) — el job lee el flag de
            # daemon_state, mismo patrón que stop_requested (fix 3.1).
            await self._monitor_job.read_monitor_running()

        # Heartbeat como tarea supervisada (T27/T28)
        async def _schedule_heartbeat() -> None:
            # APScheduler 3.11 ejecuta jobs async con asyncio.ensure_future (T1) —
            # esperar la tarea supervisada evita corrutinas huérfanas (was never
            # awaited) y permite el drenaje por registro (T27/T28).
            await create_supervised_task(_heartbeat_job(), name="heartbeat")

        self.scheduler.add_job(
            _schedule_heartbeat,
            "interval",
            seconds=hb_seconds,
            id="heartbeat",
            max_instances=1,
        )

        # 2.1-r2: job de DISCO (T65, 15-30 min) — productor de downloads_paused
        # y de monitor.disk_warning (reanuda automáticamente al liberar espacio).
        async def _disk_job() -> None:
            engine = self._engine
            if engine is None:
                return
            from sqlalchemy.ext.asyncio import async_sessionmaker

            from tikdown_rs.core.disk import disk_job as disk_job_fn

            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as s:
                await disk_job_fn(s, self.settings)

        async def _schedule_disk() -> None:
            await create_supervised_task(_disk_job(), name="disk-job")

        self.scheduler.add_job(
            _schedule_disk,
            "interval",
            seconds=self.settings.disk_check_interval_seconds,
            id="disk-check",
            max_instances=1,
        )

        # 2.1-r2: PROBE DE RED (§9/e07s01) — máquina de estados online/offline;
        # el evento network_available gobierna las descargas (L-D2: nace seteado,
        # sin monitor la red se asume disponible).
        if self._network_monitor is None:
            from tikdown_rs.core.network_monitor import NetworkMonitor

            self._network_monitor = NetworkMonitor(self.settings)

        async def _network_probe_job() -> None:
            monitor = self._network_monitor
            if monitor is None:
                return
            await monitor.probe()  # probe_fn=None → HEAD httpx real (§1: nunca TikTok)

        async def _schedule_network() -> None:
            await create_supervised_task(_network_probe_job(), name="network-probe")

        self.scheduler.add_job(
            _schedule_network,
            "interval",
            seconds=self.settings.network_probe_interval_seconds,
            id="network-probe",
            max_instances=1,
        )

        # e13s01: recogida automática de backfills 'queued' + 'paused' reanudables
        async def _backfill_collect_job() -> None:
            engine = self._engine
            if engine is None:
                return
            from sqlalchemy.ext.asyncio import async_sessionmaker

            from tikdown_rs.core.crypto import decrypt_cookie, load_or_create_fernet_key
            from tikdown_rs.core.download_engine import YtDlpEngine
            from tikdown_rs.core.tasks import create_supervised_task
            from tikdown_rs.services import backfill as backfill_svc
            from tikdown_rs.services.cookies import working_cookies_list

            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as s:
                cookie = await working_cookies_list(s)

                # bug #17: el daemon NUNCA construía un YtDlpEngine — pasaba el
                # AsyncEngine SQLAlchemy como motor de descarga (AttributeError).
                # Construir el engine real con la cookie descifrada (F-01/F-15).
                blob = None
                if cookie:
                    key = load_or_create_fernet_key(self.settings.data_dir / "fernet.key")
                    blob = decrypt_cookie(cookie[0].encrypted_blob, key)
                downloader = YtDlpEngine(cookies_blob=blob)

                # El job lanza la recogida como tarea supervisada (T27) — el
                # estado de red del daemon se consulta vía el monitor (default online)
                async def _run() -> None:
                    async with maker() as s2:
                        await backfill_svc.collect_queued_backfills(
                            s2,
                            engine=downloader,
                            cookies=cookie,
                            owner="daemon",
                        )

                create_supervised_task(_run(), name="backfill-collect")

        async def _schedule_backfill() -> None:
            await create_supervised_task(_backfill_collect_job(), name="backfill-collect-job")

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

        # on_event=None (auditoría 3.2-B): el bus de eventos NO está conectado
        # al bot — los jobs (monitor/disco/red/backfill) emiten vía callbacks
        # inyectables pero send_event es un noop (ver README).
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
    try:
        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)
    except (ValueError, OSError):  # pragma: no cover - no main thread
        pass
    asyncio.run(runner._lifecycle())

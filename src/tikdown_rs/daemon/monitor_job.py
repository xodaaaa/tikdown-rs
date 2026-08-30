"""Job del monitor de cuentas — daemon/monitor_job.py (e03s02, hallazgo 2.1-r2).

El ciclo del monitor existía (services/monitor.py, testeado) pero NADIE lo
llamaba: `monitor start` escribía monitor_running en DB y el heartbeat jamás
lo leía. Este componente cierra el circuito:

- `read_monitor_running()` — invocado por el HEARTBEAT del daemon (run.py):
  lee el flag de daemon_state (mismo patrón que stop_requested, fix 3.1) y
  arranca/drena el loop de ciclos en caliente.
- `run_loop()` — mientras running, dispara `run_monitor_cycle()` (services)
  con el discover del daemon (`daemon_discover`: extrae feed vía YtDlpEngine
  y registra vídeos nuevos como Video, respetando el throttle L-G1).
- `stop()` — drena el loop como tarea supervisada (T28), sin colgar el daemon.

story: e03s02
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from tikdown_rs.core.config import Settings

LOG = logging.getLogger("tikdown_rs.monitor_job")


class MonitorJob:
    """Gestiona el ciclo del monitor comandado por daemon_state.monitor_running."""

    def __init__(
        self,
        maker: async_sessionmaker,
        settings: Settings | None = None,
        interval_seconds: float | None = None,
        cycle_fn=None,
        discover_fn=None,
    ) -> None:
        self._maker = maker
        self.settings = settings
        self.running = False
        self.task: asyncio.Task | None = None
        self._interval = interval_seconds  # inyectable (tests); default: Settings
        self._cycle_fn = cycle_fn  # inyectable (tests); default: services
        self._discover_fn = discover_fn  # inyectable (tests); default: daemon_discover

    def _default_interval(self) -> float:
        if self._interval is not None:
            return self._interval
        minutes = self.settings.monitor_interval_minutes if self.settings else 5
        return float(minutes * 60)

    def _cycle(self):
        if self._cycle_fn is not None:
            return self._cycle_fn
        from tikdown_rs.services.monitor import run_monitor_cycle

        return run_monitor_cycle

    def _discover(self):
        if self._discover_fn is not None:
            return self._discover_fn
        return self.daemon_discover

    async def read_monitor_running(self) -> None:
        """Lee monitor_running y transiciona el loop (heartbeat → job, 2.1)."""
        from sqlalchemy import select

        from tikdown_rs.models.models import DaemonState

        async with self._maker() as s:
            row = (
                await s.execute(select(DaemonState).where(DaemonState.id == 1))
            ).scalar_one_or_none()
            want = bool(row.monitor_running) if row else False
        if want and not self.running:
            self.running = True
            self.task = asyncio.ensure_future(self.run_loop())
            LOG.info("monitor_job.started")
        elif not want and self.running:
            self.running = False
            LOG.info("monitor_job.stopping")

    async def run_loop(self) -> None:
        """Bucle de ciclos mientras running (tarea supervisada, T27/T28)."""
        interval = self._default_interval()
        while self.running:
            try:
                await self._run_one_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - un ciclo fallido no mata el job
                LOG.warning("monitor_job.cycle_error", exc_info=True)
            # dormir en trozos cortos para reaccionar al stop en caliente
            waited = 0.0
            while self.running and waited < interval:
                await asyncio.sleep(min(0.5, interval - waited))
                waited += 0.5

    async def _run_one_cycle(self) -> None:
        async with self._maker() as session:
            await self._cycle()(session, self._discover())

    async def join(self, timeout: float = 10.0) -> None:
        """Espera el drenaje del loop tras stop (T28)."""
        if self.task is not None:
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(self.task), timeout=timeout)

    async def stop(self) -> None:
        """Detiene el loop (apagado del daemon o del propio job)."""
        self.running = False
        if self.task is not None and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - el drenaje nunca revienta
                LOG.warning("monitor_job.drain_error", exc_info=True)
        self.task = None

    async def daemon_discover(self, session, account) -> None:
        """discover del daemon: extrae el feed y registra vídeos nuevos (2.1).

        Extrae las entradas del perfil vía YtDlpEngine (cookies working si las
        hay, mismo esquema que backfill-collect) y persiste los vídeos aún no
        conocidos como Video(account_id=...). El monitor NO toca
        backfill_status (§10) — solo descubre y registra.
        """
        from datetime import UTC, datetime

        from sqlalchemy import select

        from tikdown_rs.core.crypto import decrypt_cookie, load_or_create_fernet_key
        from tikdown_rs.core.download_engine import YtDlpEngine
        from tikdown_rs.models.models import Video
        from tikdown_rs.services.cookies import working_cookies_list

        blob = None
        cookie_rows = await working_cookies_list(session)
        if cookie_rows and self.settings is not None:
            key = load_or_create_fernet_key(self.settings.data_dir / "fernet.key")
            blob = decrypt_cookie(cookie_rows[0].encrypted_blob, key)
        downloader = YtDlpEngine(cookies_blob=blob)

        profile = await downloader.extract_profile(account.username)
        entries = profile.get("entries") or []
        ids = [e.get("id") for e in entries if e.get("id")]
        if not ids:
            return
        existing = set(
            (
                await session.execute(
                    select(Video.tiktok_video_id).where(Video.tiktok_video_id.in_(ids))
                )
            ).scalars()
        )
        now = datetime.now(UTC).isoformat()
        for entry in entries:
            vid = entry.get("id")
            if not vid or vid in existing:
                continue
            session.add(
                Video(
                    tiktok_video_id=str(vid),
                    account_id=account.id,
                    title=(entry.get("title") or "")[:500] or None,
                    upload_date=entry.get("upload_date"),
                    duration=entry.get("duration"),
                    status="new",
                    created_at=now,
                    updated_at=now,
                )
            )
            LOG.info(
                "monitor.video_discovered",
                extra={"account": account.username, "video": vid},
            )
        await session.commit()

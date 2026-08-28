"""Monitor de red — core/network_monitor.py (§9).

Máquina de estados online/offline con probe HEAD a endpoints neutrales.
network_available (asyncio.Event) nace SETEADO (L-D2). network.online solo
desde offline confirmado, con la duración de la caída (T35).

story: e07s01
"""

from __future__ import annotations

import asyncio
import logging
import time

from tikdown_rs.core.config import Settings

LOG = logging.getLogger("tikdown_rs.network")

# F-13: backoff del probe offline (30s → techo 120s)
_BACKOFF_BASE = 30
_BACKOFF_CEILING = 120


def probe_backoff(failures: int) -> int:
    """Backoff del probe mientras offline: 30s → techo 120s (F-13)."""
    if failures <= 1:
        return _BACKOFF_BASE
    return min(_BACKOFF_BASE * (2 ** (failures - 1)), _BACKOFF_CEILING)


class NetworkMonitor:
    """Monitor de red con máquina de estados online/offline (§9)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = "online"
        self._consecutive_failures = 0
        self.offline_since: float | None = None
        # L-D2: el evento nace SETEADO — sin monitor, la red se asume disponible
        self.network_available = asyncio.Event()
        self.network_available.set()
        self.on_event = None  # canal de eventos (inyectado, L-I5)

    @property
    def threshold(self) -> int:
        return self.settings.network_offline_threshold_consecutive_failures

    async def _emit(self, event: str, payload: dict | None = None) -> None:
        if self.on_event is not None:
            cb = self.on_event(event, payload)
            if hasattr(cb, "__await__"):
                await cb
            else:  # canal síncrono (L-G2)
                pass

    async def record_failure(self) -> None:
        """Registra un fallo del probe; confirma offline tras el umbral."""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.threshold and self.state != "offline":
            # Caída CONFIRMADA (§9) — solo en la TRANSICIÓN (una vez)
            self.state = "offline"
            self.offline_since = time.time()
            self.network_available.clear()
            await self._emit("network.offline", {})
            LOG.warning("network.offline_confirmada")
        elif self.state == "online":
            # Probando caída (aún no confirmada — §9: un HEAD fallido no es caída)
            self.state = "probing"

    async def record_success(self) -> None:
        """Probe exitoso: si estaba offline CONFIRMADO, vuelve a online (T35)."""
        was_offline = self.state == "offline"
        self._consecutive_failures = 0
        if was_offline:
            # T35: capturar duración ANTES de limpiar el timestamp
            duration = (time.time() - self.offline_since) if self.offline_since else 0.0
            self.offline_since = None
            self.state = "online"
            self.network_available.set()
            await self._emit("network.online", {"duration_seconds": round(duration)})
            LOG.info("network.online", extra={"duration": round(duration)})
        elif self.state == "probing":
            self.state = "online"

    async def probe(self, probe_fn=None) -> bool:
        """Ejecuta un probe HEAD; devuelve éxito. probe_fn inyectable (tests)."""
        try:
            ok = await probe_fn() if probe_fn else await self._probe_httpx()
        except Exception:
            ok = False
        if ok:
            await self.record_success()
        else:
            await self.record_failure()
        return ok

    async def _probe_httpx(self) -> bool:
        """Probe HEAD a endpoints neutrales (nunca TikTok, §1)."""
        import httpx

        urls = [u.strip() for u in self.settings.network_probe_url.split(",") if u.strip()]
        if not urls:
            urls = ["https://example.com"]  # default razonable (§9)
        timeout = self.settings.network_probe_timeout_seconds  # F-13
        async with httpx.AsyncClient(timeout=timeout) as client:
            for url in urls:
                try:
                    resp = await client.head(url)
                    if resp.status_code < 500:
                        return True
                except Exception:
                    continue
        return False

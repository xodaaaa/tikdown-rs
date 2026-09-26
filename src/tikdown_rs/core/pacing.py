"""Global download pacing: one cross-process cooldown gate + concurrency bound.

Trampas neutralizadas: T-DB-6, T-DB-7, T-DB-12, T-ENGINE-8, T-ENGINE-26. Regla: §3.5, §4.5.

ONE gate shared by every download trigger (monitor, backfill daemon/CLI,
retries, bot actions): a persisted cross-process cooldown clock in the
``download_pacing_state`` singleton plus an in-process concurrency semaphore.
Callers arrive in M3/M4; this module delivers the pacer, the semaphore and
their contract only.

Cooldown draw: uniform in [MIN, MAX] per download, never a fixed interval
(T-ENGINE-26); MIN=MAX is the degenerate fixed case, 0/0 disables the draw
(T-ENGINE-8: engine tests disable it), MAX<MIN is a ConfigurationError
(defense in depth; Settings already rejects it).
"""

import asyncio
import random
from datetime import UTC, datetime, timedelta
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.config import Settings
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.models.download_pacing_state import read_next_allowed_at, reserve_download_slot

# Acquire() never sleeps longer than this between reservation retries, so a
# long cooldown still reacts promptly to clock moves and concurrent winners.
DEFAULT_MAX_SINGLE_SLEEP = 5.0
# Tiny epsilon so a wait of N seconds wakes marginally after N, never before.
SLEEP_EPSILON = 0.001


def _iso_utc(moment: datetime) -> str:
    """ISO8601 UTC with milliseconds timespec (T-DB-7): second granularity
    makes two processes agree on the same slot and lose the distinction."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds")


class DownloadPacer:
    """Single gate every download trigger must pass before starting a download.

    The cooldown clock lives in the ``download_pacing_state`` singleton row, so
    daemon, CLI and bot processes in front of the same DB share it. The read of
    the current slot is only a fast path: the actual claim is the atomic CAS
    UPDATE ... RETURNING in ``reserve_download_slot`` (T-DB-6/T-DB-12), so a
    busy row is never spin-written and a lost race is recomputed, never faked.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        rng: random.Random | None = None,
        min_seconds: int | None = None,
        max_seconds: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._rng = rng if rng is not None else random.Random()
        self._min = float(
            min_seconds
            if min_seconds is not None
            else settings.global_download_cooldown_min_seconds
        )
        self._max = float(
            max_seconds
            if max_seconds is not None
            else settings.global_download_cooldown_max_seconds
        )
        if self._max < self._min:
            raise ConfigurationError(
                f"cooldown max_seconds must be >= min_seconds (min={self._min}, max={self._max})"
            )
        self._disabled = self._min == 0.0 and self._max == 0.0
        self.max_single_sleep = DEFAULT_MAX_SINGLE_SLEEP

    def _utcnow(self) -> datetime:
        """UTC now; tests freeze time by replacing this bound method."""
        return datetime.now(UTC)

    async def reserve_next_slot(self) -> float:
        """Claim or inspect the next download slot; return seconds to wait (0 = go now).

        Busy path: read-only, no spin-write, no rng draw consumed. Free path:
        draw uniform in [MIN, MAX], persist ``now + draw`` through the atomic
        CAS with immediate commit. A race loss re-runs the whole reservation.
        """
        now = self._utcnow()
        now_s = _iso_utc(now)
        async with self._session_factory() as session:
            current = await read_next_allowed_at(session)
            if current is not None:
                remaining = (datetime.fromisoformat(current) - now).total_seconds()
                if remaining > 0.0:
                    return remaining
            draw = 0.0 if self._disabled else self._rng.uniform(self._min, self._max)
            won = await reserve_download_slot(
                session, now_s, _iso_utc(now + timedelta(seconds=draw))
            )
        if not won:
            # Lost a cross-process race after drawing: recompute, never proceed.
            return await self.reserve_next_slot()
        return 0.0

    async def acquire(self) -> None:
        """Block until the caller may download; the reservation is already made."""
        while True:
            wait = await self.reserve_next_slot()
            if wait <= 0.0:
                return
            await asyncio.sleep(min(wait + SLEEP_EPSILON, self.max_single_sleep))


class DownloadSemaphore:
    """In-process bound on concurrent downloads (MAX_CONCURRENT_DOWNLOADS, §4.5)."""

    def __init__(self, max_concurrent_downloads: int) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent_downloads)

    async def __aenter__(self) -> Self:
        await self._semaphore.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        self._semaphore.release()

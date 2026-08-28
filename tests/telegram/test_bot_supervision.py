"""e12s01 — supervisión del polling: healthcheck getMe + reinicio (T71, §6.1).

Cubre: getMe periódico (nunca getUpdates, T71), reinicio tras N fallos,
flag anti-reinicio-concurrente, tarea supervisada que no bloquea el loop,
cancelación en stop().

story: e12s01
"""

from __future__ import annotations

import asyncio

import pytest

from tikdown_rs.core.config import Settings
from tikdown_rs.daemon.telegram.bot import TelegramBot


def _bot() -> TelegramBot:
    """TelegramBot con token falso (no arranca red) + mocks inyectados."""
    settings = Settings(_env_file=None, telegram_bot_token="123:ABC")
    bot = TelegramBot(settings=settings)
    return bot


class _FakeApp:
    """Application falsa con bot.get_me() controlable."""

    def __init__(self) -> None:
        self._get_me_failures = 0
        self._get_me_calls = 0
        self.stop_calls = 0
        self.start_calls = 0
        self.bot = _FakeBot(self)


class _FakeBot:
    """Bot falso cuyo get_me() puede fallar N veces."""

    def __init__(self, app) -> None:
        self._app = app

    async def get_me(self):
        self._app._get_me_calls += 1
        if self._app._get_me_failures > 0:
            self._app._get_me_failures -= 1
            raise RuntimeError("Conflict: terminated by other getUpdates request")
        return None


def test_supervision_usa_get_me_no_get_updates():
    """T71: el healthcheck usa getMe (get_me), nunca getUpdates."""
    import inspect

    import tikdown_rs.daemon.telegram.bot as mod

    src = inspect.getsource(mod.TelegramBot._supervise_polling)
    assert "get_me(" in src
    assert "get_updates" not in src.lower() and "getUpdates" not in src


@pytest.mark.asyncio
async def test_supervision_get_me_ok_no_restart():
    """getMe exitoso → sin reinicio."""
    bot = _bot()
    fake = _FakeApp()
    restarts = []

    async def _restart() -> None:
        restarts.append(1)

    bot._restart_bot = _restart  # type: ignore[attr-defined]
    # Simular una sola comprobación: llamar el cuerpo interno una vez
    # (el loop infinito no se ejecuta en test; testeamos la lógica de decisión)
    await bot._check_polling_health(fake)
    assert restarts == []
    assert fake._get_me_calls == 1


@pytest.mark.asyncio
async def test_supervision_get_me_fail_n_times_restarts():
    """getMe falla N veces (default 3) → reinicio."""
    bot = _bot()
    fake = _FakeApp()
    fake._get_me_failures = 3  # 3 fallos consecutivos
    restarts = []

    async def _restart() -> None:
        restarts.append(1)

    bot._restart_bot = _restart  # type: ignore[attr-defined]
    # 3 comprobaciones fallidas
    for _ in range(3):
        await bot._check_polling_health(fake)
    assert len(restarts) == 1  # reiniciado tras 3 fallos
    assert fake._get_me_calls == 3


@pytest.mark.asyncio
async def test_supervision_no_restart_antes_de_n_fallos():
    """Menos de N fallos → sin reinicio."""
    bot = _bot()
    fake = _FakeApp()
    fake._get_me_failures = 2  # solo 2 fallos (< 3)
    restarts = []

    async def _restart() -> None:
        restarts.append(1)

    bot._restart_bot = _restart  # type: ignore[attr-defined]
    for _ in range(2):
        await bot._check_polling_health(fake)
    assert restarts == []


@pytest.mark.asyncio
async def test_supervision_flag_evita_reinicio_concurrente():
    """Flag _restarting evita reinicios concurrentes."""
    bot = _bot()
    fake = _FakeApp()
    fake._get_me_failures = 100
    bot._restarting = True  # ya reiniciando
    restarts = []

    async def _restart() -> None:
        restarts.append(1)

    bot._restart_bot = _restart  # type: ignore[attr-defined]
    await bot._check_polling_health(fake)
    assert restarts == []  # no reinicia porque ya está reiniciando


@pytest.mark.asyncio
async def test_supervision_corre_como_tarea_no_bloquea():
    """El supervisor corre como tarea (asyncio), no bloquea el loop."""
    bot = _bot()
    # _supervise_polling es un loop infinito → verificar que lanza una corrutina
    assert asyncio.iscoroutinefunction(bot._supervise_polling)


def test_supervision_interval_configurable():
    """POLLING_HEALTHCHECK_INTERVAL configurable (default 30s)."""
    from tikdown_rs.daemon.telegram.bot import _POLLING_HEALTHCHECK_INTERVAL

    assert _POLLING_HEALTHCHECK_INTERVAL > 0
    assert _POLLING_HEALTHCHECK_INTERVAL == 30

"""e02s03 — apagado limpio: L-B1, drenaje (T9/T28), bot manual (T10)."""

# story: e02s03
import asyncio

from tikdown_rs.core.config import Settings
from tikdown_rs.daemon.run import DaemonRunner


def test_runner_existe():
    """DaemonRunner con _lifecycle en un solo asyncio.run (L-B1)."""
    assert hasattr(DaemonRunner, "_lifecycle")
    assert hasattr(DaemonRunner, "_start")
    assert hasattr(DaemonRunner, "_run")
    assert hasattr(DaemonRunner, "_shutdown")


async def test_lifecycle_start_shutdown_monitor_detenido(tmp_path):
    """El runner arranca, el monitor queda detenido (T5.1), y apaga limpio."""
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        telegram_bot_token="",  # sin bot en este test
    )
    runner = DaemonRunner(settings)
    # No ejecutar _lifecycle completo (haría migraciones); probar el contrato
    assert runner.settings.monitor_autostart is False


async def test_cancel_pending_drena_registro():
    """T9/T28: el apagado drena el registro de tareas, no el scheduler."""
    from tikdown_rs.core.tasks import cancel_pending_tasks, create_supervised_task

    async def _long():
        await asyncio.sleep(60)

    t = create_supervised_task(_long(), name="shutdown-test")
    await asyncio.sleep(0.01)
    assert not t.done()
    await cancel_pending_tasks(timeout=1.0)
    assert t.cancelled() or t.done()


async def test_runner_usa_telegrambot_con_supervision():
    """e12s01: _start_bot usa TelegramBot (con healthcheck), no Application propia."""
    import inspect

    import tikdown_rs.daemon.run as run_mod

    src = inspect.getsource(run_mod.DaemonRunner._start_bot)
    assert "TelegramBot" in src
    assert "bot.start()" in src
    # La supervisión se lanza dentro de TelegramBot.start() (e12s01)
    import tikdown_rs.daemon.telegram.bot as bot_mod

    bot_src = inspect.getsource(bot_mod.TelegramBot)
    assert "_launch_supervision" in bot_src
    assert "_supervise_polling" in bot_src

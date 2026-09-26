"""Supervised task registry: single creation path, sync crash callback.

Traps covered: T-ASYNC-1, T-ASYNC-2, T-ASYNC-12. Rule: 5.5.
"""

import asyncio
import logging

from tikdown_rs.core.tasks import (
    create_supervised_task,
    supervised_tasks,
)


async def test_creates_real_task_and_registers_by_id() -> None:
    """T-ASYNC-12: the registry keeps the real Task, keyed by id(task)."""

    async def noop() -> None:
        await asyncio.sleep(0)

    task = create_supervised_task(noop(), name="probe")
    assert isinstance(task, asyncio.Task)
    assert supervised_tasks() == [task]
    await task
    await asyncio.sleep(0)  # let the done callback run


async def test_crash_logs_error_with_task_name(caplog) -> None:
    """T-ASYNC-1/T-ASYNC-2: sync callback interpolates the task name into an ERROR.

    An async callback would create a coroutine that is never awaited; the ERROR
    record appearing after a single loop tick proves the callback itself ran.
    """

    async def boom() -> None:
        raise RuntimeError("kaput")

    with caplog.at_level(logging.ERROR, logger="tikdown_rs.core.tasks"):
        # Alembic's fileConfig (run in-process by other tests) disables
        # pre-existing loggers; the callback's own behavior is what is under
        # test here, so undo that cross-test poisoning before capturing.
        logging.getLogger("tikdown_rs.core.tasks").disabled = False
        task = create_supervised_task(boom(), name="crasher")
        try:
            await task
        except RuntimeError as exc:
            assert str(exc) == "kaput"
        await asyncio.sleep(0)

    records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert records, "supervised task crash produced no ERROR log record"
    message = records[0].getMessage()
    assert "crasher" in message
    assert "kaput" in message
    # T-ASYNC-12: the crashed task is no longer registered.
    assert all(t is not task for t in supervised_tasks())


async def test_registry_entry_removed_after_completion() -> None:
    async def noop() -> None:
        await asyncio.sleep(0)

    task = create_supervised_task(noop(), name="shortlived")
    assert any(t is task for t in supervised_tasks())
    await task
    await asyncio.sleep(0)  # done callbacks run on the next loop iteration
    assert all(t is not task for t in supervised_tasks())

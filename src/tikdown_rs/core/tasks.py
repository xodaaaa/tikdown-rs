"""Supervised background tasks: single creation path, crash audit, bounded drain.

Trampas neutralizadas: T-ASYNC-1, T-ASYNC-2, T-ASYNC-11, T-ASYNC-12. Regla: 5.5, 5.2.
Every background task in the daemon goes through create_supervised_task(); never
bare asyncio.create_task.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# T-ASYNC-12: keyed by id(task), never by name (names collide).
_registry: dict[int, asyncio.Task] = {}


def supervised_tasks() -> list[asyncio.Task]:
    """Currently registered supervised tasks (daemon status uses this later, T-ASYNC-14)."""
    return list(_registry.values())


def create_supervised_task(coro, *, name: str | None = None) -> asyncio.Task:
    """Create, register and audit a background task (5.5).

    The done callback is synchronous (T-ASYNC-1: an async callback would create a
    coroutine that is never executed) and interpolates the task name into the log
    message (T-ASYNC-2: logging rejects a ``name=`` keyword).
    """
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _registry[id(task)] = task

    def _on_done(done: asyncio.Task) -> None:
        _registry.pop(id(done), None)
        if done.cancelled():
            return
        exc = done.exception()
        if exc is not None:
            logger.error("Supervised task %s crashed: %r", done.get_name(), exc)

    task.add_done_callback(_on_done)
    return task


async def drain_supervised_tasks(timeout: float) -> None:
    """Cancel remaining supervised tasks and wait at most timeout seconds (5.2 step 7).

    T-ASYNC-11: the registry holds real Task references, so they can be cancelled.
    """
    tasks = supervised_tasks()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)

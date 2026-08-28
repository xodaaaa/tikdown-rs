"""Tareas de fondo supervisadas — core/tasks.py.

Toda tarea de fondo pasa por `create_supervised_task()` (principio §0.10),
nunca `asyncio.create_task` directo. El registro permite drenar/cancelar en el
apagado (T27/T28) — el scheduler no espera a los jobs en curso (T9).

story: e02s01
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

LOG = logging.getLogger("tikdown_rs.tasks")

# Registro de tareas activas — indexado por id(task), nunca por nombre (T30).
_task_refs: dict[int, asyncio.Task] = {}


def create_supervised_task(coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task:
    """Crea y registra una tarea de fondo supervisada.

    - Loguea cualquier excepción con contexto (nunca structlog, L-B4).
    - `add_done_callback` es SÍNCRONO (T1): un callback async crea la corrutina
      pero nunca la ejecuta.
    - Registro indexado por id(task) (T30).
    """
    task = asyncio.create_task(coro, name=name)
    _task_refs[id(task)] = task

    def _audit(done_task: asyncio.Task) -> None:
        # SÍNCRONO (T1): lee task.exception() para auditar el resultado.
        _task_refs.pop(id(done_task), None)
        if done_task.cancelled():
            LOG.info("task.cancelled", extra={"task": name})
            return
        exc = done_task.exception()
        if exc is not None:
            LOG.error(
                "task.failed name=%s exc=%r",
                name,
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    task.add_done_callback(_audit)
    LOG.debug("task.started", extra={"task": name})
    return task


async def cancel_pending_tasks(timeout: float = 5.0) -> None:
    """Cancela explícitamente las tareas pendientes del registro (T28).

    Es el drenaje real del apagado (T27): AsyncIOScheduler.shutdown(wait=True)
    no espera a los jobs en curso (T9), así que el registro es el único
    mecanismo que espera/cancela trabajo antes de disponer recursos.
    """
    pending = list(_task_refs.values())
    if not pending:
        return
    LOG.info("tasks.draining", extra={"count": len(pending)})
    for task in pending:
        if not task.done():
            task.cancel()
    await asyncio.wait(pending, timeout=timeout)
    # Las que sigan vivas tras el timeout quedan registradas (el loop las cierra)
    remaining = [t for t in pending if not t.done()]
    if remaining:
        LOG.warning("tasks.drain_timeout", extra={"remaining": len(remaining)})

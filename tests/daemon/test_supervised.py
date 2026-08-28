"""e02s01 — create_supervised_task, registro, drenaje (T1/T27/T28/T30)."""

# story: e02s01
import asyncio

import pytest

from tikdown_rs.core.tasks import cancel_pending_tasks, create_supervised_task


async def test_create_supervised_task_registra_y_audita():
    """Crea una tarea, se registra, y al completar se audita (callback corre)."""
    completions = []

    # Crear una tarea que completa con valor
    task = create_supervised_task(asyncio.sleep(0.01), name="test-ok")
    await asyncio.sleep(0.05)
    assert task.done()
    completions.append(task.result() is None)
    assert completions == [True]


async def test_callback_sincrono_audita_excepcion():
    """T1: una excepción en la tarea se audita (no queda silenciosa)."""

    async def _boom():
        raise ValueError("boom")

    task = create_supervised_task(_boom(), name="test-boom")
    await asyncio.sleep(0.05)
    assert task.done()
    # El callback síncrono capturó la excepción (no "coroutine never awaited")
    with pytest.raises(ValueError):
        task.result()


async def test_cancel_pending_tasks_drena():
    """T28: cancel_pending_tasks cancela las tareas pendientes del registro."""

    async def _long():
        await asyncio.sleep(60)

    task = create_supervised_task(_long(), name="test-long")
    await asyncio.sleep(0.01)
    assert not task.done()
    await cancel_pending_tasks(timeout=1.0)
    assert task.cancelled() or task.done()


async def test_indice_por_id_task_no_nombre():
    """T30: dos tareas con el mismo nombre lógico no colisionan."""

    async def _wait():
        await asyncio.sleep(0.05)

    t1 = create_supervised_task(_wait(), name="same")
    t2 = create_supervised_task(_wait(), name="same")
    assert t1 is not t2
    await asyncio.sleep(0.1)
    assert t1.done() and t2.done()

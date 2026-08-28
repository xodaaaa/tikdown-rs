"""Migraciones idempotentes — core/migrations.py.

Comprueba alembic_version antes de decidir stamp vs upgrade (T29), lock de
fichero entre procesos (T68), localización de alembic.ini/alembic/ por
candidatos (T70). env.py async (T51) vive en alembic/env.py.

story: e01s04
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from pathlib import Path

LOG = logging.getLogger("tikdown_rs.migrations")

try:  # fcntl en Unix, msvcrt en Windows (T68)
    import fcntl

    def _lock_file(fd: int, exclusive: bool) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_UN)

except ImportError:  # pragma: no cover - Windows
    import msvcrt

    def _lock_file(fd: int, exclusive: bool) -> None:
        # msvcrt.locking exige un fd entero (no TextIOWrapper)
        if exclusive:
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def _find_alembic_ini() -> Path:
    """Localiza alembic.ini por candidatos (T70).

    1. Junto al módulo (dev editable: la raíz del repo).
    2. cwd (imagen Docker: COPY . . + WORKDIR /app).
    Nunca Path(__file__).resolve().parents[1] (L-J4: se rompe en wheel).
    """
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "alembic.ini",  # raíz del repo (dev)
        Path.cwd() / "alembic.ini",  # cwd (Docker)
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    raise FileNotFoundError(
        "No se encontró alembic.ini. Buscado en: "
        + ", ".join(str(c) for c in candidates)
        + ". Ver trampa T70 — el recurso debe localizarse por candidatos."
    )


def apply_migrations(db_url: str) -> None:
    """Aplica migraciones pendientes de forma idempotente y segura (T29/T68).

    Se ejecuta bajo un lock de fichero en DATA_DIR (.migrate.lock) para que dos
    procesos (daemon + CLI) no compitan sobre alembic_version y las DDL (T68).

    Si se llama desde un event loop async (p. ej. el arranque del daemon),
    delega a un thread con su propio loop — Alembic usa asyncio.run() en
    env.py y no puede ejecutarse dentro de un loop ya activo.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # sin loop activo — ejecutar directo
    else:
        # Loop activo: correr en thread con su propio loop (seguro desde daemon async)
        _run_in_thread(_apply_migrations_locked, db_url)
        return
    _apply_migrations_locked(db_url)


async def apply_migrations_async(db_url: str) -> None:
    """Variante async: delega a un thread para no bloquear el loop (T68 lock)."""
    await asyncio.to_thread(apply_migrations, db_url)


def _run_in_thread(fn, *args):
    """Ejecuta fn en un thread nuevo con su propio event loop."""
    result: dict = {}

    def _target() -> None:
        try:
            result["v"] = fn(*args)
        except Exception as exc:  # noqa: BLE001
            result["e"] = exc

    t = threading.Thread(target=_target)
    t.start()
    t.join()
    if "e" in result:
        raise result["e"]
    return result.get("v")


def _apply_migrations_locked(db_url: str) -> None:
    """Aplica migraciones bajo el lock de fichero (T68)."""
    alembic_ini = _find_alembic_ini()
    lock_path = alembic_ini.parent / ".migrate.lock"

    # Crear el directorio padre de la DB si no existe (L-C9) — Alembic no lo hace.
    if "///" in db_url:
        db_path = db_url.split("///", 1)[1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "wb") as lock_fd:
        _lock_file(lock_fd.fileno(), True)
        try:
            _run_alembic(db_url, alembic_ini)
        finally:
            with contextlib.suppress(Exception):  # pragma: no cover
                _lock_file(lock_fd.fileno(), False)


def _run_alembic(db_url: str, alembic_ini: Path) -> None:
    """Ejecuta alembic upgrade head programáticamente (async env, T51)."""
    # El env.py lee sqlalchemy.url del alembic.ini; para usar db_url real lo
    # pasamos vía variable de entorno que env.py lee (o sobreescribimos la opción).
    os.environ["TIKDOWN_DB_URL"] = db_url
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(alembic_ini))
    # env.py puede leer TIKDOWN_DB_URL para sobreescribir sqlalchemy.url
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")
    del os.environ["TIKDOWN_DB_URL"]

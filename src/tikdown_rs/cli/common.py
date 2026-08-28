"""Common de la CLI — cli/common.py (T18, F-21, §5.5/§5.6).

Centraliza: wrapper asyncio.run() (T18), run_or_exit() (F-21), y
prepare_invocation() que aplica migraciones idempotentes (§5.5) y construye
Settings fresca por invocación (§5.6).

story: e08s01
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable

from tikdown_rs.core.config import ConfigurationError, Settings

# Errores de negocio → ERROR <mensaje> + exit 1 (F-21)
_BUSINESS_ERRORS = (ConfigurationError, ValueError)


def run_sync[T](coro: Awaitable[T]) -> T:
    """Wrapper central de asyncio.run() para comandos síncronos (T18)."""
    return asyncio.run(coro)


def run_or_exit[T](fn: Callable[[], T]) -> T:
    """Ejecuta fn; convierte errores de negocio en ERROR + exit 1 (F-21).

    Sin tracebacks: mensaje limpio + exit code 1.
    """
    try:
        return fn()
    except _BUSINESS_ERRORS as exc:
        print(f"ERROR {exc}")
        sys.exit(1)


def prepare_invocation() -> Settings:
    """Migraciones idempotentes (§5.5, T29/T68/T70) + Settings fresca (§5.6).

    Se ejecuta al inicio de cada comando de negocio (no en --version/healthcheck,
    R10). Las migraciones corren en thread si hay loop (L-B3).
    """
    settings = Settings(_env_file=None)

    def _migrate() -> None:
        from tikdown_rs.core.migrations import apply_migrations

        db_url = f"sqlite+aiosqlite:///{settings.data_dir / 'tikdown-rs.db'}"
        apply_migrations(db_url)  # T29/T68/T70 (idempotente, lock, candidatos)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _migrate()
    else:
        asyncio.run(_migrate())
    return settings

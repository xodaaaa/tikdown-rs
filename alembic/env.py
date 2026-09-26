"""Alembic environment: async template (the sync default does not work with aiosqlite).

Trampas neutralizadas: T-DEPLOY-2. Regla: §5.4.
"""

import asyncio
import logging
from logging.config import fileConfig

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

config = context.config

# fileConfig only when the config came from an ini file (T-DEPLOY-2 guard).
if config.config_file_name is not None:
    # disable_existing_loggers=False alone does not neutralize T-DEPLOY-6 (the
    # reapply-with-force step after migrations does), but it avoids disabling
    # loggers imported before this point.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Alembic chatter stays at WARNING (§5.4).
logging.getLogger("alembic").setLevel(logging.WARNING)

from tikdown_rs.models import (
    Base,
    daemon_state,  # noqa: F401  (registers tables on metadata)
)

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )

    async def run() -> None:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
        await connectable.dispose()

    asyncio.run(run())


run_async_migrations()

"""Alembic environment, wired for async SQLAlchemy and the `app` schema."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importing the models package registers every table on Base.metadata; without it
# autogenerate would cheerfully emit a migration that drops everything.
import app.models  # noqa: F401
from app.core.config import get_settings
from app.db.base import APP_SCHEMA, Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return get_settings().DATABASE_URL


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Keep autogenerate inside the `app` schema.

    `novaretail` (Phase 5) is populated by a seeding script and deliberately not
    under Alembic's control; without this filter autogenerate would try to drop it.
    """
    if type_ == "table":
        schema = getattr(obj, "schema", None) or APP_SCHEMA
        return schema == APP_SCHEMA
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=include_object,
        version_table_schema=APP_SCHEMA,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # Nothing may be executed on `connection` before this point. Alembic makes its
    # own `begin_transaction()` a no-op when the connection is already inside a
    # transaction, in which case the migration is never committed and silently
    # rolls back when the connection closes.
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=APP_SCHEMA,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()

    engine = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    # The Alembic version table lives in `app`, so the schema has to exist before
    # Alembic connects. Committed on its own so the migration connection starts
    # clean — see the note in `do_run_migrations`. This is what lets
    # `alembic upgrade head` work against any empty database, not only one created
    # by the container bootstrap script.
    async with engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{APP_SCHEMA}"'))

    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

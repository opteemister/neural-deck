"""Alembic environment for the async SQLite application database."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from app.config import Settings, load_settings
from app.db import Base, database_url
from app.models import entities as _model_entities  # noqa: F401
from app.paths import build_paths, prepare_data_dirs
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def runtime_settings() -> Settings:
    """Use settings supplied by the embedded app when available."""

    configured = config.attributes.get("llm_studio_settings")
    return configured if isinstance(configured, Settings) else load_settings()


def run_migrations_offline() -> None:
    settings = runtime_settings()
    paths = build_paths(settings)
    prepare_data_dirs(paths)
    url = database_url(paths.sqlite_path)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    settings = runtime_settings()
    paths = build_paths(settings)
    prepare_data_dirs(paths)
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = database_url(paths.sqlite_path)
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

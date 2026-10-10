"""Async SQLite database setup shared by FastAPI and Alembic."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import event, text
from sqlalchemy.engine import ExceptionContext
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import Settings
from .paths import DataPaths

logger = logging.getLogger(__name__)

ALEMBIC_CONFIG_PATH = Path(__file__).resolve().parents[1] / "alembic.ini"


class Base(DeclarativeBase):
    """Declarative metadata root for future application models."""


def database_url(sqlite_path: Path) -> str:
    """Return an async SQLite URL for an absolute path."""

    return f"sqlite+aiosqlite:///{sqlite_path}"


def create_engine(sqlite_path: Path, *, echo: bool = False) -> AsyncEngine:
    engine = create_async_engine(
        database_url(sqlite_path),
        echo=echo,
        future=True,
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(connection: Any, _record: Any) -> None:
        # Readers of history must not prevent the metrics writer from committing.
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    @event.listens_for(engine.sync_engine, "handle_error")
    def report_connection_error(context: ExceptionContext) -> None:
        if context.is_disconnect:
            logger.warning(
                "SQLite: соединение потеряно; исключено из пула "
                "(error_type=%s, during_checkout=%s)",
                type(context.original_exception).__name__,
                context.is_pre_ping,
            )

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def migrate_database(settings: Settings, paths: DataPaths) -> None:
    """Apply bundled Alembic migrations before serving requests.

    Alembic's environment is synchronous around an async SQLAlchemy engine, so
    the command runs in a worker thread.  The settings object is passed
    through ``Config.attributes`` instead of reading process environment
    variables again; this keeps tests and embedded application instances
    deterministic.
    """

    def upgrade() -> None:
        config = Config(str(ALEMBIC_CONFIG_PATH))
        # Embedded migrations must preserve the host application's handlers.
        config.attributes["configure_logging"] = False
        config.attributes["neural_deck_settings"] = settings
        config.attributes["neural_deck_paths"] = paths
        command.upgrade(config, "head")

    await asyncio.to_thread(upgrade)


async def check_database(engine: AsyncEngine) -> None:
    """Run a real database round trip for health/status checks."""

    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


def sqlalchemy_info(engine: AsyncEngine) -> dict[str, Any]:
    """Small diagnostics payload that is safe to expose in logs/tests."""

    return {"dialect": engine.dialect.name, "driver": engine.dialect.driver}

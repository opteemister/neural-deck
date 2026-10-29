"""Closed pooled SQLite connections are replaced; writes are never silently retried."""

import logging
from types import SimpleNamespace

import pytest
from app.container import AppContainer, RuntimeDiagnostic
from app.db import check_database, create_engine
from app.process.manager import ProcessManager
from sqlalchemy import text
from sqlalchemy.exc import OperationalError


@pytest.fixture(autouse=True)
def capture_database_logger(caplog):
    # Application integration tests configure their own non-propagating handlers.
    logger = logging.getLogger("app.db")
    logger.addHandler(caplog.handler)
    yield
    logger.removeHandler(caplog.handler)


@pytest.mark.asyncio
async def test_closed_idle_connection_is_replaced_before_query(tmp_path, caplog):
    engine = create_engine(tmp_path / "db.sqlite")
    try:
        async with engine.connect() as connection:
            raw = await connection.get_raw_connection()
            driver = raw.driver_connection
            await connection.execute(text("SELECT 1"))
        # Reproduce a pool entry whose underlying aiosqlite connection died.
        await driver.close()
        with caplog.at_level(logging.WARNING, logger="app.db"):
            await check_database(engine)
        async with engine.connect() as connection:
            fresh = await connection.get_raw_connection()
            assert fresh.driver_connection is not driver
        assert "SQLite: соединение потеряно" in caplog.text
        assert "during_checkout=True" in caplog.text
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_disconnect_during_write_rolls_back_and_next_request_recovers(tmp_path, caplog):
    engine = create_engine(tmp_path / "db.sqlite")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE changes (value INTEGER)"))
        with caplog.at_level(logging.WARNING, logger="app.db"):
            with pytest.raises(OperationalError):
                async with engine.begin() as connection:
                    await connection.execute(text("INSERT INTO changes VALUES (1)"))
                    raw = await connection.get_raw_connection()
                    await raw.driver_connection.close()
                    await connection.execute(text("INSERT INTO changes VALUES (2)"))
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT COUNT(*) FROM changes"))).scalar() == 0
        assert "during_checkout=False" in caplog.text
        await check_database(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_process_diagnostic_clears_only_after_successful_scan(tmp_path):
    manager = ProcessManager(log_root=tmp_path)

    class Store:
        broken = True

        async def list(self):
            if self.broken:
                raise OperationalError("SELECT snapshot FROM model_runs", {}, Exception("closed"))
            return []

    store = Store()
    manager.run_store = store

    async def empty_discovery(**kwargs):
        return []

    manager.external_discovery = empty_discovery
    runtime = SimpleNamespace(
        diagnostics=[RuntimeDiagnostic("process_discovery", "scan_failed", "old startup error")],
        engine_service=SimpleNamespace(scan_error=None),
        model_catalogue=SimpleNamespace(scan_error=None),
        process_manager=manager,
    )
    try:
        with pytest.raises(OperationalError):
            await manager.refresh()
        diagnostics = AppContainer.diagnostic_dicts(runtime)
        assert len(diagnostics) == 1
        assert diagnostics[0]["component"] == "process_discovery"
        assert "closed" in diagnostics[0]["message"]
        store.broken = False
        await manager.refresh()
        assert AppContainer.diagnostic_dicts(runtime) == []
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_cancelled_scan_keeps_previous_diagnostic(tmp_path):
    import asyncio

    manager = ProcessManager(log_root=tmp_path)
    manager.discovery_error = "previous failure"

    class Store:
        async def list(self):
            raise asyncio.CancelledError()

    manager.run_store = Store()
    try:
        with pytest.raises(asyncio.CancelledError):
            await manager.refresh()
        assert manager.discovery_error == "previous failure"
    finally:
        await manager.close()

from __future__ import annotations

import asyncio

import pytest
from app.db import create_engine
from sqlalchemy import text


@pytest.mark.asyncio
async def test_history_reader_does_not_block_writer_commit(tmp_path):
    engine = create_engine(tmp_path / 'concurrency.db')
    try:
        async with engine.begin() as connection:
            await connection.execute(text('CREATE TABLE samples (value INTEGER)'))
            await connection.execute(text('INSERT INTO samples VALUES (1)'))
        async with engine.connect() as reader:
            # Hold a real SQLite read transaction, rather than SQLAlchemy's
            # implicit transaction (legacy sqlite mode does not BEGIN on SELECT).
            await reader.execute(text('BEGIN'))
            assert (await reader.execute(text('SELECT sum(value) FROM samples'))).scalar() == 1

            async def write():
                async with engine.begin() as writer:
                    await writer.execute(text('INSERT INTO samples VALUES (2)'))

            await asyncio.wait_for(write(), timeout=2)
            assert (await reader.execute(text('SELECT sum(value) FROM samples'))).scalar() == 1
            await reader.rollback()
        async with engine.connect() as connection:
            assert (await connection.execute(text('SELECT sum(value) FROM samples'))).scalar() == 3
    finally:
        await engine.dispose()

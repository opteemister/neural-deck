"""Durable snapshot of currently owned model processes (not profile configuration)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import BigInteger, Text, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ModelRunRecord(Base):
    __tablename__ = "model_runs"

    pid: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    process_start_time_ns: Mapped[int] = mapped_column(BigInteger)
    snapshot: Mapped[str] = mapped_column(Text)


class RunStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def list(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = await connection.execute(text("SELECT snapshot FROM model_runs"))
            return [json.loads(row[0]) for row in rows]

    async def save(self, snapshot: dict[str, Any]) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT OR REPLACE INTO model_runs (pid, process_start_time_ns, snapshot) "
                    "VALUES (:pid, :start_ns, :snapshot)"
                ),
                {
                    "pid": snapshot["instance"]["pid"],
                    "start_ns": snapshot["instance"]["process_start_time_ns"],
                    "snapshot": json.dumps(snapshot),
                },
            )

    async def delete(self, pid: int, start_ns: int | None) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM model_runs WHERE pid = :pid AND process_start_time_ns = :start_ns"
                ),
                {"pid": pid, "start_ns": start_ns},
            )

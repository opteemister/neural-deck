"""Conversation history and attachments in their own disposable SQLite database."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import event, text
from sqlalchemy.pool import NullPool

from ..db import check_database, create_engine, create_session_factory
from ..errors import AppError


class ChatStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        # Fresh connections use SQLite's native file creation after a manual
        # deletion; no pooled connection keeps writing to an unlinked old file.
        self.engine = create_engine(path, poolclass=NullPool)
        self.sessions = create_session_factory(self.engine)

        @event.listens_for(self.engine.sync_engine, "connect")
        def create_tables(connection: Any, _record: Any) -> None:
            cursor = connection.cursor()
            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS chat_conversations (
                        id TEXT PRIMARY KEY, payload TEXT NOT NULL
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS chat_attachments (
                        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                        name TEXT NOT NULL, media_type TEXT NOT NULL,
                        content BLOB, provider_id TEXT
                    )
                """)
            finally:
                cursor.close()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        await check_database(self.engine)

    async def close(self) -> None:
        await self.engine.dispose()

    async def get(self, conversation_id: str) -> dict[str, Any] | None:
        async with self.sessions() as session:
            payload = await session.scalar(
                text("SELECT payload FROM chat_conversations WHERE id=:id"), {"id": conversation_id}
            )
        return json.loads(payload) if payload is not None else None

    async def list_conversations(self) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        text("""
                SELECT id,
                    json_extract(payload, '$.turns[0].prompt') AS title,
                    json_extract(payload, '$.turns[0].attachments[0].name') AS filename,
                    json_extract(payload, '$.turns[#-1].model') AS model,
                    json_extract(payload, '$.turns[#-1].created_at') AS updated_at,
                    json_extract(payload, '$.pending') IS NOT NULL AS busy
                FROM chat_conversations
                ORDER BY updated_at DESC, id
            """)
                    )
                )
                .mappings()
                .all()
            )
        return [
            {
                "id": row["id"],
                "title": " ".join((row["title"] or row["filename"] or "Новый чат").split())[:160],
                "model": row["model"],
                "updated_at": row["updated_at"],
                "busy": bool(row["busy"]),
            }
            for row in rows
        ]

    async def delete_conversation(self, conversation_id: str) -> None:
        async with self.sessions() as session:
            await session.execute(
                text("DELETE FROM chat_attachments WHERE conversation_id=:id"),
                {"id": conversation_id},
            )
            await session.execute(
                text("DELETE FROM chat_conversations WHERE id=:id"), {"id": conversation_id}
            )
            await session.commit()

    async def save(self, conversation: dict[str, Any]) -> None:
        payload = json.dumps(conversation, ensure_ascii=False)
        async with self.sessions() as session:
            await session.execute(
                text("""
                INSERT INTO chat_conversations(id, payload) VALUES (:id, :payload)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload
            """),
                {"id": conversation["id"], "payload": payload},
            )
            await session.commit()

    async def attach(
        self,
        conversation_id: str,
        name: str,
        media_type: str,
        *,
        content: bytes | None = None,
        provider_id: str | None = None,
    ) -> str:
        asset_id = uuid4().hex
        async with self.sessions() as session:
            await session.execute(
                text("""
                INSERT INTO chat_attachments
                (id, conversation_id, name, media_type, content, provider_id)
                VALUES (:id, :owner, :name, :type, :content, :provider)
            """),
                {
                    "id": asset_id,
                    "owner": conversation_id,
                    "name": name,
                    "type": media_type,
                    "content": content,
                    "provider": provider_id,
                },
            )
            await session.commit()
        return asset_id

    async def attachment(self, conversation_id: str, asset_id: str) -> dict[str, Any]:
        async with self.sessions() as session:
            row = (
                (
                    await session.execute(
                        text("""
                SELECT * FROM chat_attachments WHERE id=:id AND conversation_id=:owner
            """),
                        {"id": asset_id, "owner": conversation_id},
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise AppError(
                "chat_asset_not_found", "Вложение этого диалога не найдено", status_code=404
            )
        return dict(row)

    @staticmethod
    def asset_url(conversation_id: str, asset_id: str) -> str:
        return f"/api/v1/chat/{conversation_id}/assets/{asset_id}"

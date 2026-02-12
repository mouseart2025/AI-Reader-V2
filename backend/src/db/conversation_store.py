"""CRUD operations for conversations and messages tables."""

import json
import uuid

from src.db.sqlite_db import get_connection


async def create_conversation(
    novel_id: str,
    title: str = "新对话",
) -> dict:
    """Create a new conversation and return it."""
    conv_id = str(uuid.uuid4())
    conn = await get_connection()
    try:
        await conn.execute(
            """
            INSERT INTO conversations (id, novel_id, title)
            VALUES (?, ?, ?)
            """,
            (conv_id, novel_id, title),
        )
        await conn.commit()
        cursor = await conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conv_id,)
        )
        row = await cursor.fetchone()
        return dict(row)
    finally:
        await conn.close()


async def list_conversations(novel_id: str) -> list[dict]:
    """List all conversations for a novel, newest first."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT c.*, (
                SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id
            ) as message_count
            FROM conversations c
            WHERE c.novel_id = ?
            ORDER BY c.updated_at DESC
            """,
            (novel_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def get_conversation(conversation_id: str) -> dict | None:
    """Get a single conversation by ID."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT * FROM conversations WHERE id = ?",
            (conversation_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def update_conversation_title(
    conversation_id: str, title: str
) -> None:
    """Update conversation title."""
    conn = await get_connection()
    try:
        await conn.execute(
            """
            UPDATE conversations
            SET title = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (title, conversation_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def delete_conversation(conversation_id: str) -> None:
    """Delete a conversation and all its messages (CASCADE)."""
    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM conversations WHERE id = ?",
            (conversation_id,),
        )
        await conn.commit()
    finally:
        await conn.close()


async def add_message(
    conversation_id: str,
    role: str,
    content: str,
    sources_json: str | None = None,
) -> dict:
    """Add a message to a conversation."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            INSERT INTO messages (conversation_id, role, content, sources_json)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, role, content, sources_json),
        )
        msg_id = cursor.lastrowid
        # Update conversation timestamp
        await conn.execute(
            """
            UPDATE conversations SET updated_at = datetime('now')
            WHERE id = ?
            """,
            (conversation_id,),
        )
        await conn.commit()
        row_cursor = await conn.execute(
            "SELECT * FROM messages WHERE id = ?", (msg_id,)
        )
        row = await row_cursor.fetchone()
        return dict(row)
    finally:
        await conn.close()


async def list_messages(
    conversation_id: str, limit: int = 100
) -> list[dict]:
    """List messages in a conversation, oldest first."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT id, conversation_id, role, content, sources_json, created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (conversation_id, limit),
        )
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            d = dict(row)
            if d.get("sources_json"):
                d["sources"] = json.loads(d["sources_json"])
            else:
                d["sources"] = []
            del d["sources_json"]
            results.append(d)
        return results
    finally:
        await conn.close()


async def get_recent_messages(
    conversation_id: str, limit: int = 10
) -> list[dict]:
    """Get the most recent messages for context building."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT role, content FROM messages
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (conversation_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in reversed(rows)]
    finally:
        await conn.close()

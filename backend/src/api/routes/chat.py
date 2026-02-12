"""Chat conversation management REST endpoints."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from src.db import conversation_store, novel_store


router = APIRouter(tags=["chat"])


class CreateConversationRequest(BaseModel):
    title: str = "新对话"


# ── Conversations ────────────────────────────

@router.get("/api/novels/{novel_id}/conversations")
async def list_conversations(novel_id: str):
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    convs = await conversation_store.list_conversations(novel_id)
    return {"conversations": convs}


@router.post("/api/novels/{novel_id}/conversations")
async def create_conversation(novel_id: str, req: CreateConversationRequest):
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    conv = await conversation_store.create_conversation(novel_id, req.title)
    return conv


@router.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    conv = await conversation_store.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")
    await conversation_store.delete_conversation(conversation_id)
    return {"ok": True}


# ── Messages ─────────────────────────────────

@router.get("/api/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: str, limit: int = Query(100)):
    conv = await conversation_store.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")
    messages = await conversation_store.list_messages(conversation_id, limit)
    return {"messages": messages, "conversation": conv}


@router.get("/api/conversations/{conversation_id}/export")
async def export_conversation(conversation_id: str):
    """Export a conversation as Markdown."""
    conv = await conversation_store.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")
    messages = await conversation_store.list_messages(conversation_id, limit=10000)

    lines: list[str] = []
    lines.append(f"# {conv['title']}")
    lines.append("")
    lines.append(f"> 创建于 {conv['created_at']}  ")
    lines.append(f"> 共 {len(messages)} 条消息")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in messages:
        role_label = "**用户**" if msg["role"] == "user" else "**AI**"
        lines.append(f"### {role_label}")
        lines.append("")
        lines.append(msg["content"])
        if msg.get("sources") and len(msg["sources"]) > 0:
            chapters_str = ", ".join(f"第{ch}章" for ch in msg["sources"])
            lines.append("")
            lines.append(f"*来源: {chapters_str}*")
        lines.append("")

    md = "\n".join(lines)
    title = conv["title"].replace('"', "'")
    return PlainTextResponse(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{title}.md"'},
    )

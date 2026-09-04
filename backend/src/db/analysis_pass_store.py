"""CRUD operations for analysis_passes / pass_chapter_facts tables (multi-pass 独立二审 MVP, issue #70 Epic 1).

影子存储:二审进度与产物全部落在这两张表,不碰主表 chapter_facts。
"""

import json
import logging

from src.db.sqlite_db import get_connection
from src.models.chapter_fact import ChapterFact

logger = logging.getLogger(__name__)

# pass 类型:MVP 只有 source_pass(独立二审);review_pass 为二期预留
PASS_KIND_SOURCE = "source_pass"

_PASS_COLS = (
    "id, novel_id, kind, model_name, status, chapter_start, chapter_end,"
    " current_chapter, config_json, history_json, created_at, updated_at, completed_at"
)


def _row_to_pass(row) -> dict:
    """把 analysis_passes 行转成 dict,并解析 config_json/history_json。"""
    result = dict(row)
    for key in ("config_json", "history_json"):
        raw = result.get(key)
        try:
            result[key] = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            result[key] = {}
    return result


def _initial_history(kind: str, model_name: str | None, provider: str | None) -> dict:
    """pass history 埋点初始结构 (Story 1.2)。

    chapters 以章节号为键,每章记录: source range(chapter_id + 字符区间)、
    source-only 完成时间戳、AIR unlock 时间戳(MVP 由 Epic 3 diff 生成动作
    充当 unlock 时回填)、findings 计数、diff 候选计数(Epic 3 回填)、
    人工裁决计数(Epic 4 回填)。未发生的事件保持 None/0,不产生脏数据。
    """
    return {
        "kind": kind,
        "model": model_name,
        "provider": provider,
        "source_only_completed_at": None,
        "chapters": {},
    }


# 每章 history 记录的字段骨架(Story 1.2 确认清单)
_CHAPTER_HISTORY_SKELETON: dict = {
    "chapter_id": None,      # chapters 表主键
    "char_start": None,      # 本章实际参与分析的字符区间 [start, end)
    "char_end": None,
    "is_truncated": False,
    "completed_at": None,    # 本章 source-only 独立抽取完成时间戳
    "findings": {},          # source-only findings 计数
    "air_unlocked_at": None, # AIR unlock 时间戳(Epic 3 diff 生成时回填)
    "diff_counts": {"air_only": None, "pass_only": None, "different": None},
    # 人工最终裁决(Epic 4 回填):confirmed=采纳一审, rejected=采纳二审,
    # neither=两者皆否;adjudication_log 保留逐条明细(entry_id/verdict/时间)
    "adjudication": {"confirmed": 0, "rejected": 0, "neither": 0},
    "adjudication_log": [],
}


async def create_pass(
    pass_id: str,
    novel_id: str,
    chapter_start: int,
    chapter_end: int,
    kind: str = PASS_KIND_SOURCE,
    model_name: str | None = None,
    config: dict | None = None,
    provider: str | None = None,
) -> None:
    """Create a new analysis pass with status=running.

    current_chapter 初始化为 chapter_start - 1(尚无已处理章节),
    使 resume 的 current_chapter + 1 语义在任何时刻都正确。
    """
    history = _initial_history(kind, model_name, provider)
    conn = await get_connection()
    try:
        await conn.execute(
            """
            INSERT INTO analysis_passes
                (id, novel_id, kind, model_name, status, chapter_start, chapter_end,
                 current_chapter, config_json, history_json)
            VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
            """,
            (
                pass_id, novel_id, kind, model_name, chapter_start, chapter_end,
                chapter_start - 1,
                json.dumps(config or {}, ensure_ascii=False),
                json.dumps(history, ensure_ascii=False),
            ),
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_pass(pass_id: str) -> dict | None:
    """Retrieve a pass by ID (config_json/history_json 已解析为 dict)。"""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            f"SELECT {_PASS_COLS} FROM analysis_passes WHERE id = ?",
            (pass_id,),
        )
        row = await cursor.fetchone()
        return _row_to_pass(row) if row else None
    finally:
        await conn.close()


async def list_passes(novel_id: str, kind: str | None = None) -> list[dict]:
    """List passes for a novel (optionally filtered by kind), newest first."""
    conn = await get_connection()
    try:
        if kind:
            cursor = await conn.execute(
                f"SELECT {_PASS_COLS} FROM analysis_passes"
                " WHERE novel_id = ? AND kind = ? ORDER BY created_at DESC",
                (novel_id, kind),
            )
        else:
            cursor = await conn.execute(
                f"SELECT {_PASS_COLS} FROM analysis_passes"
                " WHERE novel_id = ? ORDER BY created_at DESC",
                (novel_id,),
            )
        rows = await cursor.fetchall()
        return [_row_to_pass(r) for r in rows]
    finally:
        await conn.close()


async def get_active_pass(novel_id: str, kind: str = PASS_KIND_SOURCE) -> dict | None:
    """Get the currently running or paused pass for a novel (复用单活语义)。"""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            f"SELECT {_PASS_COLS} FROM analysis_passes"
            " WHERE novel_id = ? AND kind = ? AND status IN ('running', 'paused')"
            " ORDER BY created_at DESC LIMIT 1",
            (novel_id, kind),
        )
        row = await cursor.fetchone()
        return _row_to_pass(row) if row else None
    finally:
        await conn.close()


async def update_pass_status(pass_id: str, status: str) -> None:
    """Update pass status (running/paused/completed/failed/cancelled)。

    终态(completed/failed/cancelled)同时写 completed_at。
    """
    conn = await get_connection()
    try:
        if status in ("completed", "failed", "cancelled"):
            await conn.execute(
                "UPDATE analysis_passes SET status = ?, completed_at = datetime('now'),"
                " updated_at = datetime('now') WHERE id = ?",
                (status, pass_id),
            )
        else:
            await conn.execute(
                "UPDATE analysis_passes SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (status, pass_id),
            )
        await conn.commit()
    finally:
        await conn.close()


async def update_pass_progress(pass_id: str, current_chapter: int) -> None:
    """Update the current chapter being processed."""
    conn = await get_connection()
    try:
        await conn.execute(
            "UPDATE analysis_passes SET current_chapter = ?, updated_at = datetime('now') WHERE id = ?",
            (current_chapter, pass_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def delete_pass(pass_id: str) -> bool:
    """Delete a pass; pass_chapter_facts 随行级联删除(影子数据整体清除)。"""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "DELETE FROM analysis_passes WHERE id = ?",
            (pass_id,),
        )
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()


async def upsert_pass_chapter_fact(
    pass_id: str,
    chapter_id: int,
    fact: ChapterFact,
    status: str = "completed",
    error: str | None = None,
) -> None:
    """Insert or replace a pass chapter fact (UNIQUE(pass_id, chapter_id) UPSERT)。

    fact_json 与 chapter_facts 同构(ChapterFact JSON,中文不转义),
    保证 Epic 3 diff 可直接比较。
    """
    conn = await get_connection()
    try:
        await conn.execute(
            """
            INSERT OR REPLACE INTO pass_chapter_facts
                (pass_id, chapter_id, fact_json, status, error)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                pass_id,
                chapter_id,
                fact.model_dump_json(ensure_ascii=False),
                status,
                error,
            ),
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_pass_chapter_facts(pass_id: str) -> list[dict]:
    """读取本 pass 已完成章节的 facts(影子表),按章节号排序。

    返回结构与 chapter_fact_store.get_all_chapter_facts 兼容的最小子集
    ({"chapter_id", "chapter_num", "fact", ...}),供 ContextSummaryBuilder
    的 facts_provider 直接消费;failed 章节不进 context。
    """
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT p.chapter_id AS chapter_pk, c.chapter_num AS chapter_num,
                   p.fact_json, p.status, p.created_at
            FROM pass_chapter_facts p
            JOIN chapters c ON c.id = p.chapter_id
            WHERE p.pass_id = ? AND p.status = 'completed'
            ORDER BY c.chapter_num
            """,
            (pass_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "chapter_id": row["chapter_num"],
                "chapter_num": row["chapter_num"],
                "fact": json.loads(row["fact_json"]),
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        await conn.close()


async def get_pass_chapter_fact(pass_id: str, chapter_id: int) -> dict | None:
    """读取本 pass 单个章节的 fact(影子表),含 status/error;不存在返回 None。

    chapter_id 为 chapters 表主键(与 upsert_pass_chapter_fact 写入口径一致)。
    与 get_pass_chapter_facts 的差异:不按 status='completed' 过滤,由调用方
    (Epic 3 diff)自行判定失败/未完成章节。
    """
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT p.chapter_id AS chapter_pk, c.chapter_num AS chapter_num,
                   p.fact_json, p.status, p.error, p.created_at
            FROM pass_chapter_facts p
            JOIN chapters c ON c.id = p.chapter_id
            WHERE p.pass_id = ? AND p.chapter_id = ?
            """,
            (pass_id, chapter_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "chapter_id": row["chapter_pk"],
            "chapter_num": row["chapter_num"],
            "fact": json.loads(row["fact_json"]),
            "status": row["status"],
            "error": row["error"],
            "created_at": row["created_at"],
        }
    finally:
        await conn.close()


async def get_completed_chapter_nums(pass_id: str) -> set[int]:
    """本 pass 已完成章节的章节号集合(resume/续跑时跳过)。"""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT c.chapter_num
            FROM pass_chapter_facts p
            JOIN chapters c ON c.id = p.chapter_id
            WHERE p.pass_id = ? AND p.status = 'completed'
            """,
            (pass_id,),
        )
        rows = await cursor.fetchall()
        return {row["chapter_num"] for row in rows}
    finally:
        await conn.close()


async def update_pass_history(pass_id: str, **fields) -> None:
    """顶层 history 字段合并(只更新给定键,其余不动)。"""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT history_json FROM analysis_passes WHERE id = ?",
            (pass_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return
        history = json.loads(row["history_json"]) if row["history_json"] else {}
        history.update(fields)
        await conn.execute(
            "UPDATE analysis_passes SET history_json = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(history, ensure_ascii=False), pass_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def update_chapter_history(pass_id: str, chapter_num: int, entry: dict) -> None:
    """每章 history 埋点合并:history.chapters[chapter_num].update(entry)。

    新章节键自动带上 Story 1.2 的完整字段骨架(未发生的事件为 None/0),
    保证「每章何时完成独立抽取、何时解锁对照、差异多少、人裁了多少」
    对任何章节都能一致地回答。
    """
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT history_json FROM analysis_passes WHERE id = ?",
            (pass_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return
        history = json.loads(row["history_json"]) if row["history_json"] else {}
        chapters = history.setdefault("chapters", {})
        record = chapters.setdefault(str(chapter_num), dict(_CHAPTER_HISTORY_SKELETON))
        record.update(entry)
        await conn.execute(
            "UPDATE analysis_passes SET history_json = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(history, ensure_ascii=False), pass_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def recover_stale_passes() -> int:
    """Mark any 'running' passes as 'paused' on server startup.

    与 analysis_task_store.recover_stale_tasks 同语义:重启后 running 状态
    的 pass 没有活动循环驱动,重置为 paused 供用户从 current_chapter+1 续跑。

    Returns the number of passes recovered.
    """
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            UPDATE analysis_passes
            SET status = 'paused', updated_at = datetime('now')
            WHERE status = 'running'
            """
        )
        await conn.commit()
        count = cursor.rowcount
        if count > 0:
            logger.info("Recovered %d stale running pass(es) → paused", count)
        return count
    finally:
        await conn.close()

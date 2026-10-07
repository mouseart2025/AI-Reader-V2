"""章节状态词表迁移测试:旧版本残留的 'done' 必须归一为 'completed'。

旧版本(约 v0.6x 及更早)用 'done' 标记已分析章节,现行代码统一写
'completed'。残留 'done' 行会让书架进度统计为 0(误显示「未分析」),
且分析跳过逻辑只认 'completed',会把全书重新抽取。init_db 的迁移
步骤必须把 'done' 归一为 'completed',且可重复执行(幂等)。
"""

import pytest

import src.db.sqlite_db as sqlite_db
from src.db.sqlite_db import init_db


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


def _conn_factory(memory_db):
    async def _factory():
        return _NonClosing(memory_db)

    return _factory


async def _seed(memory_db) -> None:
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", ("n-legacy", "旧书")
    )
    await memory_db.execute(
        "INSERT INTO chapters (id, novel_id, chapter_num, title, content, analysis_status)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (1, "n-legacy", 1, "第1章", "正文", "done"),
    )
    await memory_db.execute(
        "INSERT INTO chapters (id, novel_id, chapter_num, title, content, analysis_status)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (2, "n-legacy", 2, "第2章", "正文", "pending"),
    )
    await memory_db.commit()


@pytest.mark.asyncio
async def test_legacy_done_status_normalized(memory_db, monkeypatch):
    """init_db 的迁移把残留 'done' 归一为 'completed',其他状态不动。"""
    await _seed(memory_db)
    monkeypatch.setattr(
        sqlite_db, "get_connection", _conn_factory(memory_db)
    )

    await init_db()

    rows = await memory_db.execute_fetchall(
        "SELECT chapter_num, analysis_status FROM chapters WHERE novel_id = ?"
        " ORDER BY chapter_num",
        ("n-legacy",),
    )
    assert [tuple(r) for r in rows] == [(1, "completed"), (2, "pending")]

    # 幂等:再跑一次不产生变化
    await init_db()
    rows = await memory_db.execute_fetchall(
        "SELECT chapter_num, analysis_status FROM chapters WHERE novel_id = ?"
        " ORDER BY chapter_num",
        ("n-legacy",),
    )
    assert [tuple(r) for r in rows] == [(1, "completed"), (2, "pending")]


@pytest.mark.asyncio
async def test_progress_counts_normalized_status(memory_db, monkeypatch):
    """迁移后 novel_store 的进度统计必须能数到这些章节。"""
    from src.db import novel_store

    monkeypatch.setattr(
        novel_store, "get_connection", _conn_factory(memory_db)
    )

    await memory_db.execute(
        "INSERT INTO novels (id, title, total_chapters, total_words) VALUES (?, ?, ?, ?)",
        ("n-progress", "旧书", 2, 100),
    )
    for num in (1, 2):
        await memory_db.execute(
            "INSERT INTO chapters (id, novel_id, chapter_num, title, content, analysis_status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (num, "n-progress", num, f"第{num}章", "正文", "completed"),
        )
    await memory_db.commit()

    novels = await novel_store.list_novels()
    target = [n for n in novels if n["id"] == "n-progress"]
    assert target and target[0]["analysis_progress"] == 1.0

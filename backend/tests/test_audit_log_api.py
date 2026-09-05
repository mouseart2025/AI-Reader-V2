"""C3: 审计日志查询 API 全流程测试 (issue #70)。

路由函数直接调用(参照 test_analysis_passes_api.py 模式):memory_db +
monkeypatch 各写入模块的 AUDIT_LOG_PATH 到 tmp_path。

覆盖:写入 → 按 type/chapter 查询 → 过滤/倒序/limit → 404/400;
大文件尾部读取;旧格式(无 novel_id)记录兼容。
"""

import json

import pytest
from fastapi import HTTPException

from src.api.routes import audit_log as audit_routes
from src.api.routes.audit_log import get_audit_log
from src.db import novel_store
from src.extraction import hallucination_reviewer, name_resolver
from src.services import entity_resolver


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


@pytest.fixture
def api_env(memory_db, monkeypatch, tmp_path):
    """patch novel_store.get_connection;三类审计日志重定向到 tmp_path。"""

    async def _factory():
        return _NonClosing(memory_db)

    monkeypatch.setattr(novel_store, "get_connection", _factory)
    paths = {
        "name_resolution": tmp_path / "name_resolution_log.jsonl",
        "entity_resolution": tmp_path / "entity_resolution_log.jsonl",
        "hallucination": tmp_path / "hallucination_review_log.jsonl",
    }
    monkeypatch.setattr(name_resolver, "AUDIT_LOG_PATH", paths["name_resolution"])
    monkeypatch.setattr(entity_resolver, "AUDIT_LOG_PATH", paths["entity_resolution"])
    monkeypatch.setattr(hallucination_reviewer, "AUDIT_LOG_PATH",
                        paths["hallucination"])
    return memory_db, paths


async def _seed(memory_db) -> None:
    await memory_db.execute(
        "INSERT INTO novels (id, title, total_chapters) VALUES ('n1', '测试小说', 2)"
    )
    await memory_db.commit()


def _write(path, records: list[dict]) -> None:
    """以追加方式写 JSONL(模拟各写入方,不经过 write_audit_records 的时间戳)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


@pytest.mark.asyncio
async def test_name_resolution_full_flow(api_env):
    """写入记录 → 按 type 查询 → novel 过滤、倒序、chapter 过滤、limit。"""
    memory_db, paths = api_env
    await _seed(memory_db)
    _write(paths["name_resolution"], [
        {"novel_id": "n1", "chapter_id": 1, "field": "characters",
         "from": "行者", "to": "孙悟空", "source": "dict", "rule": "name_resolve"},
        {"novel_id": "n2", "chapter_id": 1, "field": "characters",
         "from": "八戒", "to": "猪八戒", "source": "dict", "rule": "name_resolve"},
        {"novel_id": "n1", "chapter_id": 2, "field": "characters",
         "from": "二愣子", "to": "韩立", "source": "correction", "rule": "alias_merge"},
    ])

    # 默认 type=name_resolution;只含 n1;倒序(最新在前)
    res = await get_audit_log("n1")
    assert res["count"] == 2
    assert [r["from"] for r in res["records"]] == ["二愣子", "行者"]
    assert all(r["novel_id"] == "n1" for r in res["records"])

    # chapter 过滤
    res = await get_audit_log("n1", chapter=2)
    assert [r["from"] for r in res["records"]] == ["二愣子"]
    res = await get_audit_log("n1", chapter=1)
    assert [r["from"] for r in res["records"]] == ["行者"]
    res = await get_audit_log("n1", chapter=99)
    assert res["count"] == 0

    # limit
    res = await get_audit_log("n1", limit=1)
    assert [r["from"] for r in res["records"]] == ["二愣子"]


@pytest.mark.asyncio
async def test_type_routing(api_env):
    """type=entity_resolution / hallucination 路由到对应 JSONL。"""
    memory_db, paths = api_env
    await _seed(memory_db)
    _write(paths["entity_resolution"], [
        {"novel_id": "n1", "input_cluster": ["甲", "乙"], "output_groups": []},
    ])
    _write(paths["hallucination"], [
        {"novel_id": "n1", "chapter_id": 3, "candidates": ["银驮"],
         "actions": [{"name": "银驮", "action": "removed"}]},
    ])

    res = await get_audit_log("n1", type="entity_resolution")
    assert res["count"] == 1
    assert res["records"][0]["input_cluster"] == ["甲", "乙"]

    res = await get_audit_log("n1", type="hallucination", chapter=3)
    assert res["count"] == 1
    assert res["records"][0]["candidates"] == ["银驮"]
    # chapter 过滤对无 chapter_id 的 entity_resolution 记录生效
    res = await get_audit_log("n1", type="entity_resolution", chapter=1)
    assert res["count"] == 0

    # 各 type 互不串扰
    res = await get_audit_log("n1", type="name_resolution")
    assert res["count"] == 0


@pytest.mark.asyncio
async def test_novel_not_found_404(api_env):
    memory_db, _paths = api_env
    await _seed(memory_db)
    with pytest.raises(HTTPException) as exc:
        await get_audit_log("no-such-novel")
    assert exc.value.status_code == 404
    assert exc.value.detail == "小说不存在"


@pytest.mark.asyncio
async def test_bad_type_400(api_env):
    memory_db, _paths = api_env
    await _seed(memory_db)
    with pytest.raises(HTTPException) as exc:
        await get_audit_log("n1", type="bogus")
    assert exc.value.status_code == 400
    assert "审计日志类型" in exc.value.detail


@pytest.mark.asyncio
async def test_missing_log_file_returns_empty(api_env):
    """日志文件尚不存在(从未产生决策)→ 空列表而非报错。"""
    memory_db, _paths = api_env
    await _seed(memory_db)
    res = await get_audit_log("n1")
    assert res["count"] == 0
    assert res["records"] == []


@pytest.mark.asyncio
async def test_old_format_without_novel_id_visible(api_env):
    """旧格式记录(无 novel_id 字段)无法归属,对所有小说可见(兼容读取)。"""
    memory_db, paths = api_env
    await _seed(memory_db)
    _write(paths["name_resolution"], [
        {"chapter_id": 1, "field": "characters", "from": "旧名", "to": "新名"},
        {"novel_id": "n2", "chapter_id": 1, "from": "他书", "to": "他书"},
    ])
    res = await get_audit_log("n1")
    assert [r["from"] for r in res["records"]] == ["旧名"]
    # 损坏行被容忍跳过
    with open(paths["name_resolution"], "a", encoding="utf-8") as f:
        f.write("{not json\n")
        f.write(json.dumps({"novel_id": "n1", "chapter_id": 9,
                            "from": "好的", "to": "好的2"}) + "\n")
    res = await get_audit_log("n1")
    assert [r["from"] for r in res["records"]] == ["好的", "旧名"]


@pytest.mark.asyncio
async def test_tail_read_large_file(api_env, monkeypatch):
    """大文件从尾部读取:窗口小于行长时也能正确拼装,limit 取最新 N 条。"""
    memory_db, paths = api_env
    await _seed(memory_db)
    # 调小块大小,且每条记录 > 块大小,覆盖部分行丢弃/窗口翻倍逻辑
    monkeypatch.setattr(audit_routes, "_TAIL_CHUNK", 128)
    records = [
        {"novel_id": "n1", "chapter_id": i, "seq": i, "pad": "x" * 100}
        for i in range(1, 61)
    ]
    _write(paths["name_resolution"], records)

    res = await get_audit_log("n1", limit=10)
    assert res["count"] == 10
    assert [r["seq"] for r in res["records"]] == list(range(60, 50, -1))

    # chapter 过滤需要扫到文件头(第 1 章在文件最前面)
    res = await get_audit_log("n1", chapter=1)
    assert res["count"] == 1
    assert res["records"][0]["seq"] == 1

    # 其他小说的记录即使在窗口内也被过滤
    _write(paths["name_resolution"], [
        {"novel_id": "n2", "chapter_id": 99, "seq": 999, "pad": "y" * 100},
    ])
    res = await get_audit_log("n1", limit=10)
    assert all(r["novel_id"] == "n1" for r in res["records"])
    assert [r["seq"] for r in res["records"]] == list(range(60, 50, -1))


@pytest.mark.asyncio
async def test_written_by_resolver_then_queried(api_env):
    """端到端:NameResolver 改写落盘 → API 按 chapter 查询可见。"""
    memory_db, paths = api_env
    await _seed(memory_db)
    from src.models.chapter_fact import ChapterFact, CharacterFact

    nr = name_resolver.NameResolver()
    nr._canonical_map = {"行者": "孙悟空"}
    nr._map_source = {"行者": "dict"}
    nr.resolve(
        ChapterFact(chapter_id=7, novel_id="n1",
                    characters=[CharacterFact(name="行者")]),
        log_path=paths["name_resolution"],
    )

    res = await get_audit_log("n1", chapter=7)
    assert res["count"] == 1
    rec = res["records"][0]
    assert rec["from"] == "行者" and rec["to"] == "孙悟空"
    assert rec["source"] == "dict" and rec["timestamp"]
    # 别的章节查不到
    res = await get_audit_log("n1", chapter=8)
    assert res["count"] == 0

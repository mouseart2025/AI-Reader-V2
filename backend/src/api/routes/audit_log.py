"""名字/实体决策审计日志查询 API (issue #70 provenance)。

把 audit_reports/*.jsonl 的三条既有审计通道暴露给产品:
- name_resolution   → name_resolution_log.jsonl(NameResolver 改写 +
                      FactValidator alias-merge/泛称改名,C1/C2)
- entity_resolution → entity_resolution_log.jsonl(Epic 2 LLM 聚类)
- hallucination     → hallucination_review_log.jsonl(FR-4.2 幻觉判定)

日志路径复用各写入方的 AUDIT_LOG_PATH 模块常量(__file__ 定位,与 sidecar
下写入端同一机制);读取从文件尾部按块扩展,避免全量加载大 JSONL。
旧格式记录(无 novel_id 字段)无法归属具体小说,对所有小说可见(兼容读取)。
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from src.db import novel_store
from src.extraction import hallucination_reviewer, name_resolver
from src.services import entity_resolver

router = APIRouter(prefix="/api/novels", tags=["audit"])

# type → 持有 AUDIT_LOG_PATH 常量的模块;调用时动态读取,便于测试重定向。
_LOG_MODULES = {
    "name_resolution": name_resolver,
    "entity_resolution": entity_resolver,
    "hallucination": hallucination_reviewer,
}

# 尾部读取的初始块大小(测试可 monkeypatch 调小以覆盖分块逻辑)
_TAIL_CHUNK = 256 * 1024

_MAX_LIMIT = 1000


async def _require_novel(novel_id: str) -> None:
    if not await novel_store.get_novel(novel_id):
        raise HTTPException(status_code=404, detail="小说不存在")


def _log_path(log_type: str) -> Path:
    return _LOG_MODULES[log_type].AUDIT_LOG_PATH


def _matches(rec: dict, novel_id: str, chapter: int | None) -> bool:
    # 旧格式记录没有 novel_id(或为空),无法归属 → 对所有小说可见
    rec_novel = rec.get("novel_id")
    if rec_novel and rec_novel != novel_id:
        return False
    if chapter is not None and rec.get("chapter_id") != chapter:
        return False
    return True


def _load_matching(
    path: Path, novel_id: str, chapter: int | None, limit: int,
) -> list[dict]:
    """从 JSONL 尾部读最近的匹配记录(倒序,最多 limit 条)。

    从文件尾部按 _TAIL_CHUNK 起步、每次翻倍扩大窗口;窗口内匹配不足 limit
    且未读到文件头则继续扩大。部分窗口的首行不完整,丢弃(扩大窗口后会
    重新读到完整行)。
    """
    if not path.exists():
        return []
    size = path.stat().st_size
    read_size = 0
    matched: list[dict] = []
    while read_size < size:
        read_size = min(size, max(_TAIL_CHUNK, read_size * 2))
        with open(path, "rb") as f:
            f.seek(size - read_size)
            data = f.read(read_size)
        if read_size < size:
            # 窗口未覆盖文件头:首行可能不完整,丢弃
            nl = data.find(b"\n")
            if nl < 0:
                continue  # 单行比窗口还大,继续扩大
            data = data[nl + 1:]
        matched = []
        for line in reversed(data.decode("utf-8", errors="replace").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # 容忍截断/损坏行
            if not isinstance(rec, dict):
                continue
            if _matches(rec, novel_id, chapter):
                matched.append(rec)
                if len(matched) >= limit:
                    return matched
    return matched


@router.get("/{novel_id}/audit-log")
async def get_audit_log(
    novel_id: str,
    type: str = "name_resolution",
    chapter: int | None = None,
    limit: int = 200,
):
    """查询某本小说的名字/实体决策审计记录(倒序,最新在前)。"""
    await _require_novel(novel_id)
    if type not in _LOG_MODULES:
        raise HTTPException(status_code=400, detail=f"不支持的审计日志类型: {type}")
    limit = max(1, min(limit, _MAX_LIMIT))
    records = _load_matching(_log_path(type), novel_id, chapter, limit)
    return {
        "novel_id": novel_id,
        "type": type,
        "chapter": chapter,
        "count": len(records),
        "records": records,
    }

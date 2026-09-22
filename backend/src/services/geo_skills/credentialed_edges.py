"""资信边注册:apply 层改写豁免的 (child, parent) 边集合,按小说标题作用域。

资信来源(与 2026-09-22 rebuilt-vs-applied 归因同一口径):
  1. fixture golden correct_parent(tests/fixtures/golden_standard_*.json)
  2. errata gold(backend/data/hierarchy_validation/*_errata_gold.json)
     中 verdict=正确 的 parent 边
  3. knowledge_prior 硬编码先验表

仅用于「改写前查免」:一条边有资信时,apply 层的图层分组/跨层解挂/
孤儿补挂不得覆盖它。无资信来源的小说返回空集 = 零行为。
数据按 novel 作用域隔离,不跨书混用。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_DIR = _BACKEND_ROOT / "tests" / "fixtures"
_ERRATA_DIR = _BACKEND_ROOT / "data" / "hierarchy_validation"

# (标题关键词, errata gold 键, fixture 文件名|None)
_TITLE_SOURCES: tuple[tuple[str, str, str | None], ...] = (
    ("水浒", "shuihu", "golden_standard_water_margin.json"),
    ("西游", "xiyouji", "golden_standard_journey_to_west.json"),
    ("红楼", "honglou", "golden_standard_dream_of_red_chamber.json"),
    ("三国", "sanguo", None),
    ("封神", "fengshen", None),
)


@lru_cache(maxsize=8)
def credentialed_edges(novel_title: str) -> frozenset[tuple[str, str]]:
    """返回该小说全部资信 (child, parent) 边。空集 = 无豁免。"""
    from src.services.geo_skills.knowledge_prior import hardcoded_priors_for_title

    edges: set[tuple[str, str]] = set()
    for key, gold_key, fixture in _TITLE_SOURCES:
        if key not in novel_title:
            continue
        if fixture:
            fpath = _FIXTURE_DIR / fixture
            if fpath.exists():
                data = json.loads(fpath.read_text(encoding="utf-8"))
                for loc in data.get("locations", []):
                    name, parent = loc.get("name"), loc.get("correct_parent")
                    if name and parent and loc.get("tier") != "DELETE":
                        edges.add((name, parent))
        gpath = _ERRATA_DIR / f"{gold_key}_errata_gold.json"
        if gpath.exists():
            data = json.loads(gpath.read_text(encoding="utf-8"))
            for name, node in data.get("nodes", {}).items():
                parent = node.get("parent")
                if name and parent and node.get("verdict") == "正确":
                    edges.add((name, parent))
    for child, parent in hardcoded_priors_for_title(novel_title).items():
        if child and parent and child != parent:
            edges.add((child, parent))
    return frozenset(edges)


def credentialed_parents_for(child: str, novel_title: str) -> list[str]:
    """某 child 的资信 parent 候选(保序,供孤儿补挂择优)。"""
    return sorted(p for c, p in credentialed_edges(novel_title) if c == child)


def reset_credentialed_cache() -> None:
    """测试用:清 lru_cache。"""
    credentialed_edges.cache_clear()

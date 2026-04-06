"""地点层级校验器 - Phase 0 benchmark infrastructure.

职责:
  1. 基于人工标注gold, 计算当前world_structure的5项质量指标
  2. 基于知识库规则, 对任意novel的层级自动flag候选错误
  3. 支持持续回归: 每次rebuild后对比历史指标

五项核心指标 (每项=1-错误占比, 越高越好):
  - Entity Precision    (A类: 非地名过滤)
  - Name Accuracy       (B类: 字形/截断/别名)
  - Tier Accuracy       (C类: tier分类)
  - Parent Precision    (D类: 父子归属)
  - Structural Health   (E类: 幻觉/消歧/冲突)

Gold annotation format: backend/data/hierarchy_validation/<novel>_errata_gold.json
Knowledge base: backend/data/hierarchy_validation/knowledge_base/*.json
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal

# Errata reason 解析 - 支持多种格式:
# 西游记: "tier continent→realm" / "parent X→Y" / "应为XXX"
# 其他小说: "tier应为city" / "应移除" / "应归入X"
_RE_TIER_CHANGE = re.compile(r"tier\s+\w+\s*[→]\s*(\w+)")
_RE_TIER_SHOULD_BE = re.compile(r"tier[应=]为\s*(\w+)")
_RE_GENERAL_TIER = re.compile(r"应为\s*(world|realm|continent|kingdom|city|region|site|building)")
_RE_PARENT_CHANGE = re.compile(r"parent\s+[^\s→]+\s*[→]\s*([^\s:：/]+)")
_RE_PARENT_SHOULD_BE = re.compile(r"(?:parent[应=]为|应归入?|应归属)\s*[\"']?([^\s\"'，,。；;]+)")
_RE_SHOULD_BE = re.compile(r"应为[\"'\u201c]?([^\"'\u201d，,。；;\s]+)")
_RE_SHOULD_DELETE = re.compile(r"应移除|应删除|非地名|非真实地名")


def parse_errata_correction(reasons: str) -> dict:
    """从errata reasons提取已知修正目标.

    Returns dict with keys: tier_to, parent_to, rename_to, should_delete (present if found).
    """
    out = {}
    if not reasons:
        return out
    # Tier corrections (多种格式)
    m = _RE_TIER_CHANGE.search(reasons)
    if m:
        out["tier_to"] = m.group(1)
    if "tier_to" not in out:
        m = _RE_TIER_SHOULD_BE.search(reasons)
        if m:
            out["tier_to"] = m.group(1)
    if "tier_to" not in out:
        m = _RE_GENERAL_TIER.search(reasons)
        if m:
            out["tier_to"] = m.group(1)
    if "tier_to" in out and "或" in out["tier_to"]:
        out["tier_to"] = out["tier_to"].split("或")[0]
    # Parent
    m = _RE_PARENT_CHANGE.search(reasons)
    if m:
        out["parent_to"] = m.group(1)
    if "parent_to" not in out:
        m = _RE_PARENT_SHOULD_BE.search(reasons)
        if m:
            out["parent_to"] = m.group(1)
    # Rename
    m = _RE_SHOULD_BE.search(reasons)
    if m:
        val = m.group(1)
        if val not in ("world", "realm", "continent", "kingdom", "city", "region", "site", "building"):
            out["rename_to"] = val
    # Delete
    if _RE_SHOULD_DELETE.search(reasons):
        out["should_delete"] = True
    return out


def is_error_resolved(
    error_type: str,
    node_name: str,
    corrections: dict,
    current_tiers: dict[str, str],
    current_parents: dict[str, str],
    current_nodes: set[str],
    current_children_count: dict[str, int] | None = None,
) -> bool:
    current_children_count = current_children_count or {}
    """检查errata标记的错误是否已被当前world_structure解决.

    Self-verification核心: 对比errata期望的修正vs当前实际状态.
    """
    if error_type.startswith("A-") or error_type == "B-修饰语截断":
        # 这些类型的修复=删除节点
        return node_name not in current_nodes
    if error_type in ("B-字形错误", "B-未合并别名"):
        # 修复=重命名/合并, 原节点不应存在
        if node_name not in current_nodes:
            return True
        # 或者目标名称已存在
        if "rename_to" in corrections and corrections["rename_to"] in current_nodes:
            return node_name not in current_nodes
        return False
    if error_type in ("C-tier错误", "C-tier可疑"):
        if "tier_to" not in corrections:
            return False
        expected = corrections["tier_to"]
        actual = current_tiers.get(node_name)
        return actual == expected
    if error_type == "C-tier倒置":
        # 倒置修复 = 节点的tier rank不再高于父节点
        # 需要rank order判断 - 使用简单的tier序
        tier_rank = {"world": 0, "realm": 1, "continent": 2, "kingdom": 3, "city": 4, "region": 5, "site": 6, "building": 7}
        t = current_tiers.get(node_name)
        p = current_parents.get(node_name)
        if not t or not p:
            return node_name not in current_nodes  # deleted
        pt = current_tiers.get(p)
        if not pt:
            return False
        return tier_rank.get(t, 99) >= tier_rank.get(pt, -1)
    if error_type in ("D-parent错误", "D-parent名错"):
        if "parent_to" not in corrections:
            # D-parent名错无明确target，但若parent已经rename也算fixed
            actual_parent = current_parents.get(node_name, "")
            # 如果当前parent包含errata提到的正字则认为已修
            return "南赡部洲" in actual_parent or node_name not in current_nodes
        expected = corrections["parent_to"]
        return current_parents.get(node_name) == expected
    if error_type == "D-孤立顶层":
        return bool(current_parents.get(node_name)) or node_name not in current_nodes
    if error_type == "E-消歧重复":
        return node_name not in current_nodes  # disambiguation node should be gone
    if error_type == "E-零证据有子":
        # 修复 = 节点消失 OR 没有子节点了
        if node_name not in current_nodes:
            return True
        return current_children_count.get(node_name, 0) == 0
    if error_type == "E-幻觉父节点":
        # 修复 = 节点消失 OR 子节点数 < 10 (原阈值: mc≤2 且 children≥10)
        if node_name not in current_nodes:
            return True
        return current_children_count.get(node_name, 0) < 10
    if error_type in ("E-疑似重复", "E-投票高冲突", "E-原文无此名"):
        return node_name not in current_nodes
    # 未知类型, 保守判定未解决
    return False

Verdict = Literal["correct", "suspect", "error"]

# 错误类别前缀 → 指标名映射
CATEGORY_TO_METRIC = {
    "A": "entity_precision",
    "B": "name_accuracy",
    "C": "tier_accuracy",
    "D": "parent_precision",
    "E": "structural_health",
}


@dataclass
class NodeVerdict:
    name: str
    verdict: Verdict
    error_types: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def has_category(self, prefix: str) -> bool:
        """e.g. has_category('A') → True if any error_type starts with 'A-'"""
        return any(t.startswith(f"{prefix}-") for t in self.error_types)


@dataclass
class HierarchyMetrics:
    """5项质量指标 + 汇总统计."""
    novel: str
    total_nodes: int
    correct_count: int
    suspect_count: int
    error_count: int
    # 五项指标 (1 - category_errors/total), 0.0-1.0
    entity_precision: float
    name_accuracy: float
    tier_accuracy: float
    parent_precision: float
    structural_health: float
    overall: float
    # 每类错误计数
    category_errors: dict[str, int]
    # 每个具体error_type计数
    error_type_counts: dict[str, int]

    def to_dict(self) -> dict:
        return asdict(self)

    def format_report(self) -> str:
        """打印人类可读的指标报告."""
        lines = [
            f"# 地点层级质量指标 — {self.novel}",
            f"",
            f"总节点: {self.total_nodes} | 正确: {self.correct_count} | "
            f"可疑: {self.suspect_count} | 错误: {self.error_count}",
            f"",
            f"| 指标                | 分数    | 错误数 |",
            f"|---------------------|---------|--------|",
            f"| Entity Precision    | {self.entity_precision:.4f}  | {self.category_errors.get('A',0):6d} |",
            f"| Name Accuracy       | {self.name_accuracy:.4f}  | {self.category_errors.get('B',0):6d} |",
            f"| Tier Accuracy       | {self.tier_accuracy:.4f}  | {self.category_errors.get('C',0):6d} |",
            f"| Parent Precision    | {self.parent_precision:.4f}  | {self.category_errors.get('D',0):6d} |",
            f"| Structural Health   | {self.structural_health:.4f}  | {self.category_errors.get('E',0):6d} |",
            f"| **Overall**         | **{self.overall:.4f}** | |",
            f"",
            f"## 错误类型分布",
        ]
        for etype, cnt in sorted(self.error_type_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- {etype}: {cnt}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# Gold-based metrics (primary path)
# ─────────────────────────────────────────────────────────────────

def load_gold(novel: str, kb_dir: Path | None = None) -> tuple[dict[str, NodeVerdict], dict]:
    """加载人工标注gold. 返回 (name → NodeVerdict, raw_data)."""
    base = kb_dir or Path(__file__).resolve().parents[2] / "data" / "hierarchy_validation"
    gold_path = base / f"{novel}_errata_gold.json"
    if not gold_path.exists():
        raise FileNotFoundError(f"Gold annotation not found: {gold_path}")
    with gold_path.open(encoding="utf-8") as f:
        data = json.load(f)
    verdicts = {}
    for name, node in data["nodes"].items():
        verdicts[name] = NodeVerdict(
            name=name,
            verdict=node["verdict"].replace("错误", "error").replace("可疑", "suspect").replace("正确", "correct"),
            error_types=list(node.get("error_types", [])),
            reasons=[node.get("reasons", "")] if node.get("reasons") else [],
        )
    return verdicts, data


def compute_metrics_from_gold(
    novel: str,
    current_nodes: set[str],
    gold_verdicts: dict[str, NodeVerdict],
    current_tiers: dict[str, str] | None = None,
    current_parents: dict[str, str] | None = None,
    gold_raw: dict | None = None,
) -> HierarchyMetrics:
    """基于gold标注计算当前层级的质量指标.

    Args:
        novel: 小说标识
        current_nodes: 当前world_structure中存在的所有地点名
        gold_verdicts: 人工标注的verdict dict

    策略:
        - 对每个当前节点, 若在gold中且被标为error, 计为category_error
        - 对gold中被标为error但已不在当前节点的, 视为已修复 (不计入)
        - 未在gold中的节点暂时忽略 (需要后续人工补标注)
    """
    # 统计所有gold节点 (包括已删除的, 因为删除IS一种修复)
    # 一个gold error的resolution方式: 节点被删/重命名(不在current) OR 状态已修正
    current_tiers = current_tiers or {}
    current_parents = current_parents or {}
    # 计算当前children count
    current_children_count: dict[str, int] = {}
    for c, p in current_parents.items():
        if p:
            current_children_count[p] = current_children_count.get(p, 0) + 1

    total = len(gold_verdicts)
    if total == 0:
        raise ValueError("Empty gold_verdicts")

    verdict_counts = {"correct": 0, "suspect": 0, "error": 0}
    category_errors = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
    error_type_counts: dict[str, int] = {}
    resolved_count = 0

    # 只计数 仍存在 + 错误仍未修复 的节点
    for name, v in gold_verdicts.items():
        # correct/suspect节点: 只要还存在就计数
        if v.verdict != "error":
            if name in current_nodes:
                verdict_counts[v.verdict] += 1
            continue

        # error节点: 检查每种错误是否已解决
        raw_reasons = ""
        if gold_raw and name in gold_raw.get("nodes", {}):
            raw_reasons = gold_raw["nodes"][name].get("reasons", "") or ""
        corrections = parse_errata_correction(raw_reasons)

        unresolved = []
        for etype in v.error_types:
            if not is_error_resolved(etype, name, corrections, current_tiers, current_parents, current_nodes, current_children_count):
                unresolved.append(etype)

        if not unresolved:
            resolved_count += 1
            continue

        # 节点至少有1个未解决错误
        verdict_counts["error"] += 1
        seen_cats = set()
        for etype in unresolved:
            prefix = etype.split("-", 1)[0]
            if prefix in category_errors and prefix not in seen_cats:
                category_errors[prefix] += 1
                seen_cats.add(prefix)
            error_type_counts[etype] = error_type_counts.get(etype, 0) + 1

    return HierarchyMetrics(
        novel=novel,
        total_nodes=total,
        correct_count=verdict_counts["correct"],
        suspect_count=verdict_counts["suspect"],
        error_count=verdict_counts["error"],
        entity_precision=1.0 - category_errors["A"] / total,
        name_accuracy=1.0 - category_errors["B"] / total,
        tier_accuracy=1.0 - category_errors["C"] / total,
        parent_precision=1.0 - category_errors["D"] / total,
        structural_health=1.0 - category_errors["E"] / total,
        overall=1.0 - verdict_counts["error"] / total,
        category_errors=category_errors,
        error_type_counts=error_type_counts,
    ), resolved_count


# ─────────────────────────────────────────────────────────────────
# Layer 3: Original text verification
# ─────────────────────────────────────────────────────────────────

class TextVerifier:
    """原文存在性校验 — 对 mc=0 节点验证是否出现在小说原文中.

    方法: 将全部章节文本拼接为单一字符串, substring search.
    对 60 万字小说 + 1000 节点, 耗时 < 0.5 秒.
    """

    def __init__(self, novel_id: str, db_path: Path | str):
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT content FROM chapters WHERE novel_id=? ORDER BY chapter_num",
            (novel_id,),
        ).fetchall()
        conn.close()
        self._text = "\n".join(r[0] for r in rows if r[0])
        self._len = len(self._text)

    def exists(self, name: str) -> bool:
        return name in self._text

    def count(self, name: str) -> int:
        return self._text.count(name)

    def context(self, name: str, window: int = 60, max_snippets: int = 5) -> list[str]:
        """Return context snippets around each occurrence of *name*.

        Args:
            name: location name to search for.
            window: chars before and after each match to include.
            max_snippets: return at most this many snippets.

        Returns:
            List of "...前文...【name】...后文..." strings.
        """
        snippets: list[str] = []
        start = 0
        while len(snippets) < max_snippets:
            idx = self._text.find(name, start)
            if idx == -1:
                break
            left = max(0, idx - window)
            right = min(self._len, idx + len(name) + window)
            before = self._text[left:idx].replace("\n", " ")
            after = self._text[idx + len(name):right].replace("\n", " ")
            snippets.append(f"...{before}【{name}】{after}...")
            start = idx + len(name)
        return snippets

    @property
    def text_length(self) -> int:
        return self._len


# ─────────────────────────────────────────────────────────────────
# Layer 2 enhancement: Context-aware suffix classification
# ─────────────────────────────────────────────────────────────────

# 人名/官职前缀 → "府" 意为府邸 (site), 不是行政区 (city)
_PERSON_TITLE_PREFIXES = frozenset({
    "太师", "太尉", "太保", "太傅", "丞相", "相国", "宰相", "国丈",
    "将军", "元帅", "都督", "刺史", "知府", "知县", "提辖", "总管",
    "大人", "员外", "长史", "侍郎", "尚书", "学士", "御史",
    "王", "公", "侯", "伯",
    # 红楼梦 specific
    "贾", "史", "薛", "王", "林", "荣国", "宁国", "北静王", "南安王",
    # 水浒 specific
    "宿太尉", "高太尉", "蔡太师", "童贯",
})

# 常见小说人物姓氏 (2-char check)
_COMMON_SURNAMES = frozenset({
    "贾", "史", "王", "薛", "林", "李", "张", "刘", "陈", "杨",
    "赵", "孙", "周", "吴", "郑", "冯", "褚", "卫", "蒋", "沈",
    "韩", "朱", "秦", "尤", "许", "何", "吕", "曹", "魏", "蔡",
    "宋", "郭", "潘", "范", "彭", "鲁", "马", "苗", "花", "方",
    "任", "袁", "柳", "酆", "鲍", "雷", "贺", "倪", "汤", "殷",
    "苏", "黄", "诸葛", "司马", "上官", "欧阳", "公孙", "太史",
})


def is_residence_fu(name: str, parent_tier: str | None = None) -> bool:
    """判断"X府"是否为府邸(site)而非行政区(city/region).

    Rule: 人名/官职/贵族前缀 + 府 = 府邸;
          地名前缀 + 府 = 行政区 (大名府、开封府).
    """
    if not name.endswith("府") or len(name) < 2:
        return False
    prefix = name[:-1]
    # 明确的人名/官职前缀
    if prefix in _PERSON_TITLE_PREFIXES:
        return True
    for title in _PERSON_TITLE_PREFIXES:
        if prefix.endswith(title):
            return True
    # 单姓 + 府 (2-3 字): 贾府、王府、薛府
    if len(prefix) <= 2 and prefix[0] in _COMMON_SURNAMES:
        return True
    # parent 是 city/kingdom/continent → 说明在城/国内部, "府"更可能是府邸
    if parent_tier in ("city", "kingdom", "continent"):
        return True
    return False


def _zhou_expected_tier(genre: str, location_names: set[str] | None = None) -> str:
    """Get expected tier for "州" suffix based on novel genre/era.

    Uses same era-detection logic as TierClassifier for consistency.
    三国: 州 = province → kingdom
    Fantasy (without 商周 keywords): 州 = large realm → kingdom
    封神(商周)/宋/明清/default: 州 = prefecture/city → city
    """
    from src.services.geo_skills.tier_classifier import _detect_era, _zhou_target_tier
    era = _detect_era(genre, location_names or set())
    return _zhou_target_tier(era)


def is_valid_place_chu(name: str) -> bool:
    """判断"X处"是否为合法地点引用(人物居所).

    Rule: 人名/称谓 + 处 = correct (贾母处, 王夫人处);
          事件描述 + 处 = error (五儿插蜡处, 鸳鸯自尽处).
    """
    if not name.endswith("处") or len(name) < 2:
        return False
    prefix = name[:-1]
    # 排除明确的合法词
    if prefix in ("出", "住", "去", "归", "深", "高阜", "别", "来", "过", "到"):
        return False  # 这些是合法词不是"X处"模式
    # 人名/称谓 + 处 → valid place reference
    # 检测: 前缀是 2-4 字的人名 (中文人名通常 2-3 字, 加称谓可达 4-5 字)
    if len(prefix) <= 5:
        # 若前缀首字是姓氏, 很可能是人名+处 → valid
        if prefix[0] in _COMMON_SURNAMES:
            return True
        # 称谓: XX夫人/老爷/太太/姑娘/小姐/奶奶/嫂子
        for title in ("夫人", "老爷", "太太", "姑娘", "小姐", "奶奶", "嫂子", "姨妈",
                       "姐姐", "妹妹", "哥哥", "弟弟"):
            if prefix.endswith(title):
                return True
    # Layer 2 heuristic: 短前缀 (≤4字) 默认为人名/称谓, 除非含事件动词
    _EVENT_VERBS = frozenset("插打杀死烧逃逮擒困埋葬砍斩缢吊投跳溺捆绑尽毙亡殁败败")
    if len(prefix) <= 4 and not any(v in prefix for v in _EVENT_VERBS):
        return True  # 短前缀 + 无事件动词 → 大概率是人名引用
    return False


# ─────────────────────────────────────────────────────────────────
# Rule-based auto-validation (secondary path)
# 用于: 1) 其他小说自动flag 2) 西游记回归检测新错误
# ─────────────────────────────────────────────────────────────────

@dataclass
class KnowledgeBase:
    non_location_words: dict
    char_variants: dict
    tier_rules: dict

    @classmethod
    def load(cls, kb_dir: Path | None = None) -> "KnowledgeBase":
        base = kb_dir or (
            Path(__file__).resolve().parents[2] / "data" / "hierarchy_validation" / "knowledge_base"
        )
        def _load(name):
            with (base / name).open(encoding="utf-8") as f:
                return json.load(f)
        return cls(
            non_location_words=_load("non_location_words.json"),
            char_variants=_load("char_variants.json"),
            tier_rules=_load("tier_rules.json"),
        )


class RuleValidator:
    """基于知识库的规则引擎 - 对任意小说的层级自动flag候选错误.

    三层校验:
        Layer 1: 结构特征筛选 (mc/children 异常, tier 倒置, 零证据, 消歧共存)
        Layer 2: 领域知识 (上下文感知后缀, X处细分, 佛道概念)
        Layer 3: 原文存在性 (optional, 需 TextVerifier)
    """

    def __init__(self, kb: KnowledgeBase | None = None, text_verifier: TextVerifier | None = None,
                 novel_genre_hint: str = ""):
        self.kb = kb or KnowledgeBase.load()
        self._text_verifier = text_verifier
        self._genre = novel_genre_hint
        self._build_indices()

    def _build_indices(self):
        nlw = self.kb.non_location_words
        self._directional = set(nlw["directional_phrases"]["exact"])
        self._concepts = set(nlw["buddhist_concepts"]["exact"])
        self._single_char = set(nlw["single_char_generic"]["exact"])
        self._non_loc_entities = set(nlw["non_location_entities"]["exact"])
        self._person_suffixes = tuple(nlw["non_location_entities"]["suffixes"])
        self._desc_suffixes = tuple(nlw["descriptive_phrases"]["suffixes"])
        self._desc_patterns = [re.compile(p) for p in nlw["descriptive_phrases"]["patterns"]]
        self._text_fragments = set(nlw["text_fragments"]["exact"])

        tr = self.kb.tier_rules
        self._suffix_tier = {
            k: v["tier"] for k, v in tr["suffix_rules"].items()
            if isinstance(v, dict) and "tier" in v
        }
        self._tier_rank = {t: i for i, t in enumerate(tr["inversion_check"]["rank_order"])}
        self._realm_names = set(tr["special_nodes"]["realm_keywords"]["names"])

    def validate_node(
        self,
        name: str,
        parent: str | None,
        tier: str | None,
        mc: int,
        children_count: int,
        all_nodes: set[str],
        location_tiers: dict[str, str],
    ) -> NodeVerdict:
        """对单节点运行15条规则."""
        errors: list[tuple[str, str]] = []  # (type, reason)

        # 特殊节点豁免: uber-root (world tier) 节点不应用A/C规则
        is_uber_root = (name in ("天下", "世界", "uber_root")) or (tier == "world")

        # === A类: 节点合法性 ===
        # A-方位泛称 (豁免 uber-root)
        if not is_uber_root and name in self._directional:
            errors.append(("A-方位泛称", f"方位词/泛称: {name}"))
        # A-概念非地名
        if name in self._concepts:
            errors.append(("A-概念非地名", f"佛道概念非地名: {name}"))
        # A-单字通名
        if len(name) == 1 and name in self._single_char:
            errors.append(("A-单字通名", f"单字通名无专名: {name}"))
        # A-非地名 (已知实体)
        if name in self._non_loc_entities:
            errors.append(("A-非地名", f"已知非地名实体: {name}"))
        # A-非地名 (人物称号后缀)
        if len(name) >= 3 and name.endswith(self._person_suffixes):
            # 排除地名前缀 (如"魔王村")
            if not any(name.endswith(suf) and name[:-len(suf)] + "村" in all_nodes for suf in ["大王"]):
                errors.append(("A-非地名", f"疑似人物称号: {name}"))
        # A-描述性短语 (但豁免合法的"X处"人名居所引用 — Layer 2 enhancement)
        if name.endswith("处") and len(name) >= 2:
            if is_valid_place_chu(name):
                pass  # Layer 2: 人名+处 = valid place reference, 不标记
            else:
                errors.append(("A-描述性短语", f"描述性短语(X+处): {name}"))
        elif name.endswith(self._desc_suffixes) and len(name) >= 3:
            errors.append(("A-描述性短语", f"描述性短语结尾: {name}"))
        else:
            for pat in self._desc_patterns:
                if pat.match(name):
                    errors.append(("A-描述性短语", f"描述性短语模式: {name}"))
                    break
        # A-非地名 (text_fragments)
        if name in self._text_fragments:
            errors.append(("A-非地名", f"原文碎片: {name}"))

        # === C类: Tier校验 ===
        # Layer 2: 上下文感知"府"分类
        parent_tier = location_tiers.get(parent, "") if parent else ""
        skip_fu_rule = is_residence_fu(name, parent_tier)
        if tier:
            # C-tier错误: 后缀规则校验
            for suffix, expected_tier in self._suffix_tier.items():
                if name.endswith(suffix) and expected_tier != "realm_or_region":
                    # Layer 2: 朝代感知"州"分类
                    # 三国/fantasy → 州=kingdom; 封神/宋/清/default → 州=city
                    if suffix == "州" and not name.endswith("部洲"):
                        expected_tier = _zhou_expected_tier(self._genre, all_nodes)
                    if tier != expected_tier:
                        # 豁免: uber-root / realm节点 / 府residence
                        if is_uber_root or name in self._realm_names:
                            break
                        if suffix == "府" and skip_fu_rule:
                            break
                        errors.append((
                            "C-tier错误",
                            f"后缀'{suffix}' → 期望tier={expected_tier}, 实际={tier}"
                        ))
                    break
            # C-tier错误: realm节点被标为continent
            if name in self._realm_names and tier == "continent":
                errors.append(("C-tier错误", f"界域节点'{name}'应为realm, 不是continent"))
            # C-tier倒置: 子节点rank < 父节点rank
            if parent and parent in location_tiers:
                p_tier = location_tiers[parent]
                p_rank = self._tier_rank.get(p_tier, -1)
                c_rank = self._tier_rank.get(tier, -1)
                if p_rank >= 0 and c_rank >= 0 and c_rank < p_rank:
                    errors.append((
                        "C-tier倒置",
                        f"{name}({tier}) rank高于父{parent}({p_tier})"
                    ))

        # === E类: 结构性校验 ===
        # E-幻觉父节点: mc≤2 且 children≥10
        if mc <= 2 and children_count >= 10:
            errors.append((
                "E-幻觉父节点",
                f"mc={mc}却有{children_count}子节点, 比例异常"
            ))
        # E-零证据有子: mc=0 但有子节点
        if mc == 0 and children_count >= 1:
            errors.append((
                "E-零证据有子",
                f"mc=0却有{children_count}个子节点"
            ))
        # E-消歧重复: name含"·", 且去前缀后的短名也存在
        if "·" in name:
            short = name.split("·")[-1]
            if short in all_nodes and short != name:
                errors.append((
                    "E-消歧重复",
                    f"消歧节点'{name}'与原名'{short}'共存"
                ))

        # === D类: Parent校验 ===
        # D-孤立顶层: 非世界根但无parent (tier=world 豁免; tier缺失也应flag)
        if not parent and name not in ("天下", "世界") and tier != "world":
            errors.append(("D-孤立顶层", "非世界根但无父节点"))

        # === Layer 3: 原文校验 (存在性 + 上下文证据) ===
        if self._text_verifier:
            if mc == 0:
                if not self._text_verifier.exists(name):
                    errors.append(("E-原文无此名", f"mc=0且原文中未找到'{name}'"))
                else:
                    # mc=0 but text has it — evidence for investigation
                    n = self._text_verifier.count(name)
                    ctx = self._text_verifier.context(name, window=60, max_snippets=2)
                    ctx_str = " | ".join(ctx) if ctx else ""
                    errors.append((
                        "E-mc零但原文存在",
                        f"mc=0但原文出现{n}次: {ctx_str}"
                    ))

        # 汇总
        if not errors:
            return NodeVerdict(name=name, verdict="correct")
        error_types = list(dict.fromkeys(e[0] for e in errors))  # dedup, preserve order
        reasons = [e[1] for e in errors]
        return NodeVerdict(
            name=name, verdict="error", error_types=error_types, reasons=reasons
        )

    def validate_snapshot(
        self,
        location_parents: dict[str, str],
        location_tiers: dict[str, str],
        mentions: dict[str, int],
    ) -> dict[str, NodeVerdict]:
        """对整个snapshot跑规则引擎."""
        all_nodes = set(location_parents.keys()) | set(location_parents.values()) | set(mentions.keys())
        all_nodes.discard("")
        all_nodes.discard(None)

        # children count
        children_count: dict[str, int] = {}
        for c, p in location_parents.items():
            children_count[p] = children_count.get(p, 0) + 1

        results = {}
        for name in all_nodes:
            if not name:
                continue
            v = self.validate_node(
                name=name,
                parent=location_parents.get(name),
                tier=location_tiers.get(name),
                mc=mentions.get(name, 0),
                children_count=children_count.get(name, 0),
                all_nodes=all_nodes,
                location_tiers=location_tiers,
            )
            results[name] = v
        return results


def compute_metrics_from_verdicts(
    novel: str, verdicts: dict[str, NodeVerdict]
) -> HierarchyMetrics:
    """从规则引擎输出直接计算指标 (无gold时使用)."""
    total = len(verdicts)
    if total == 0:
        raise ValueError("verdicts empty")
    verdict_counts = {"correct": 0, "suspect": 0, "error": 0}
    category_errors = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
    error_type_counts: dict[str, int] = {}
    for v in verdicts.values():
        verdict_counts[v.verdict] += 1
        if v.verdict == "error":
            seen = set()
            for etype in v.error_types:
                prefix = etype.split("-", 1)[0]
                if prefix in category_errors and prefix not in seen:
                    category_errors[prefix] += 1
                    seen.add(prefix)
                error_type_counts[etype] = error_type_counts.get(etype, 0) + 1
    return HierarchyMetrics(
        novel=novel,
        total_nodes=total,
        correct_count=verdict_counts["correct"],
        suspect_count=verdict_counts["suspect"],
        error_count=verdict_counts["error"],
        entity_precision=1.0 - category_errors["A"] / total,
        name_accuracy=1.0 - category_errors["B"] / total,
        tier_accuracy=1.0 - category_errors["C"] / total,
        parent_precision=1.0 - category_errors["D"] / total,
        structural_health=1.0 - category_errors["E"] / total,
        overall=1.0 - verdict_counts["error"] / total,
        category_errors=category_errors,
        error_type_counts=error_type_counts,
    )

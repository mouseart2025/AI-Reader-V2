"""ProfileQualityChecker — 聚合后实体 Profile 异常检测与修正.

Phase 1: 纯规则检测，零 LLM 成本。
- 关系类型突变修正（取最高频类型）
- 自引用关系删除（alias 解析后 source == target）

Phase 2: LLM 聚合审查（opt-in）。
- 高频实体关系摘要 → 单次 LLM 调用 → 异常标注

在 EntityAggregator.aggregate_person() 返回前调用。
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field

from src.models.entity_profiles import PersonProfile

logger = logging.getLogger(__name__)


@dataclass
class QualityFinding:
    """单条质量检测发现。"""
    finding_type: str      # "relation_mutation" | "self_reference"
    entity_name: str
    description: str
    action_taken: str      # "auto_fixed" | "removed"
    details: dict = field(default_factory=dict)


def remove_self_references(
    profile: PersonProfile,
    alias_map: dict[str, str],
) -> list[QualityFinding]:
    """删除 alias 解析后 other_person 指向自己的关系链。"""
    canonical = alias_map.get(profile.name, profile.name)
    findings: list[QualityFinding] = []
    kept = []
    for chain in profile.relations:
        other_canonical = alias_map.get(chain.other_person, chain.other_person)
        if other_canonical == canonical or chain.other_person == profile.name:
            findings.append(QualityFinding(
                finding_type="self_reference",
                entity_name=profile.name,
                description=f"自引用关系: {chain.other_person} 实为自己的别名",
                action_taken="removed",
                details={"other_person": chain.other_person,
                         "stages": len(chain.stages)},
            ))
        else:
            kept.append(chain)
    profile.relations = kept
    return findings


def fix_relation_mutations(profile: PersonProfile) -> list[QualityFinding]:
    """修正同一人物对在不同 stage 间的关系类型突变。

    只修正"振荡"（A→B→A），不修正"干净转变"（A→B）。
    """
    findings: list[QualityFinding] = []
    for chain in profile.relations:
        if len(chain.stages) < 2:
            continue

        # Count types weighted by chapter count
        type_counts: Counter[str] = Counter()
        for stage in chain.stages:
            type_counts[stage.relation_type] += len(stage.chapters)

        distinct_types = set(s.relation_type for s in chain.stages)
        if len(distinct_types) <= 1:
            continue

        # Check for clean transition: exactly 2 types with 1 transition point
        if len(distinct_types) == 2:
            type_sequence = [s.relation_type for s in chain.stages]
            transitions = sum(
                1 for i in range(1, len(type_sequence))
                if type_sequence[i] != type_sequence[i - 1]
            )
            if transitions == 1:
                continue  # clean transition (e.g., 朋友→恋人), keep as-is

        # Mutation detected: fix all stages to dominant type
        dominant = type_counts.most_common(1)[0][0]
        for stage in chain.stages:
            if stage.relation_type != dominant:
                findings.append(QualityFinding(
                    finding_type="relation_mutation",
                    entity_name=f"{profile.name}↔{chain.other_person}",
                    description=f"关系类型突变: {stage.relation_type} → {dominant}",
                    action_taken="auto_fixed",
                    details={"from": stage.relation_type, "to": dominant,
                             "chapters": stage.chapters},
                ))
                stage.relation_type = dominant
    return findings


def check_person_profile(
    profile: PersonProfile,
    alias_map: dict[str, str],
) -> list[QualityFinding]:
    """对 PersonProfile 执行全部质量检测。修改 profile in-place。

    Returns:
        修正发现列表（用于日志/未来的质量报告）。
    """
    findings: list[QualityFinding] = []
    findings.extend(remove_self_references(profile, alias_map))
    findings.extend(fix_relation_mutations(profile))
    return findings


# ── Phase 2: LLM Aggregation Review ──────────────────


_LLM_REVIEW_PROMPT = """你是一个小说数据质量审查专家。以下是一本小说的核心角色关系摘要。
请检查是否存在以下异常：
1. 矛盾关系：同一人物对有互相冲突的关系类型（如既是父子又是敌人）
2. 身份混淆：不同角色被错误地标记为同一人的别名
3. 不合理关系：关系类型与常识严重不符

仅报告你有**高度确信**的异常。如果没有发现异常，返回空数组。

输出严格 JSON 格式：
[{{"entity": "角色名", "issue": "问题描述", "suggestion": "建议修正"}}]

## 角色关系摘要
{summary}
"""


def _build_entity_summary(
    profiles: list[PersonProfile],
    max_entities: int = 30,
) -> str:
    """Build a compressed summary of top entities for LLM review.

    Only includes entities with the most relations (high-frequency entities).
    """
    # Sort by relation count + chapter count
    scored = sorted(
        profiles,
        key=lambda p: len(p.relations) * 10 + p.stats.get("chapter_count", 0),
        reverse=True,
    )[:max_entities]

    lines: list[str] = []
    for p in scored:
        rel_parts: list[str] = []
        for chain in p.relations[:5]:  # top 5 relations per entity
            types = set(s.relation_type for s in chain.stages)
            rel_parts.append(f"{chain.other_person}({'/'.join(types)})")
        aliases = [a.name for a in p.aliases[:3]]
        alias_str = f" 别名:{','.join(aliases)}" if aliases else ""
        lines.append(f"- {p.name}{alias_str}: {', '.join(rel_parts)}")

    return "\n".join(lines)


async def llm_review_profiles(
    profiles: list[PersonProfile],
) -> list[QualityFinding]:
    """Run a single LLM call to review aggregated profiles for semantic anomalies.

    Only called when config.LLM_QUALITY_REVIEW is True.
    Returns findings without modifying profiles (flagged, not auto-fixed).
    """
    from src.infra.llm_client import get_llm_client

    if not profiles:
        return []

    summary = _build_entity_summary(profiles)
    if not summary:
        return []

    prompt = _LLM_REVIEW_PROMPT.format(summary=summary)
    llm = get_llm_client()

    try:
        result, _usage = await llm.generate(
            system="你是一个小说数据质量审查专家。请严格按照 JSON 格式输出。",
            prompt=prompt,
            temperature=0.1,
            max_tokens=2048,
            timeout=30,
        )
    except Exception:
        logger.warning("LLM quality review failed", exc_info=True)
        return []

    # Parse LLM response
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM quality review response")
            return []

    if not isinstance(result, list):
        return []

    findings: list[QualityFinding] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        entity = item.get("entity", "")
        issue = item.get("issue", "")
        suggestion = item.get("suggestion", "")
        if entity and issue:
            findings.append(QualityFinding(
                finding_type="llm_review",
                entity_name=entity,
                description=issue,
                action_taken="flagged",
                details={"suggestion": suggestion},
            ))

    if findings:
        logger.info("LLM quality review: %d findings", len(findings))

    return findings

"""幻觉人物 LLM 判定层 (Epic 4, FR-4.2)。

挂在 FactValidator 规则层之后、落库之前(analysis_service 在
validator.validate() 之后调用)。规则层(name-pattern)抓不住的疑似幻觉
人物(银驮类:名字在原文中根本找不到),结合章节上下文由 LLM 判定是否
为真实人物;高置信幻觉剔除,低置信降级为存疑(保留 + 审计标记),决策
全部落 JSONL 审计日志 (NFR-5)。

别名送审(canonical 污染防线):new_aliases 中原文不可定位且非白名单的
别名一并进入同一次 LLM 判定(单章仍 ≤1 次调用);高置信幻觉别名从
new_aliases 剔除(不动 character 本身),低置信保留并审计标记。

白名单保护(真实人物不误杀):
- protected_names(entity_dictionary 实体 + 本次运行已确立的人物)永不进入判定;
- 名字(或 "·" 消歧后的基本名,如 平顶山·樵夫 → 樵夫)能在原文中直接
  定位的人物不进入判定 — 判定的正是 name-pattern 抓不住的疑似的名字;
- LLM 返回候选集之外的裁决一律忽略。

开关: config.HALLUCINATION_REVIEW_ENABLED(默认开)。关闭时
review_chapter_characters 为 no-op,行为与 v0.73 一致 (NFR-3)。
仅当存在候选时才发起 LLM 调用(每章 ≤1 次轻量调用,NFR-2)。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.infra import config
from src.models.chapter_fact import ChapterFact

logger = logging.getLogger(__name__)

PROMPT_VERSION = "hr-char-v2"  # v2: 候选纳入 new_aliases(别名链送审)

# 决策审计日志(JSONL,每章一条)— 与 audit_reports 其他产物同目录。
AUDIT_LOG_PATH = (
    Path(__file__).resolve().parents[2] / "audit_reports" / "hallucination_review_log.jsonl"
)

_VALID_CONFIDENCE = {"high", "medium", "low"}

_SYSTEM_PROMPT = (
    "你是小说人物真实性审核专家。给你章节原文与一组疑似幻觉的人物名"
    "(这些名字在原文中找不到完全匹配的字面出现;其中可能包含别名声明,"
    "即某角色的 new_aliases 条目),判断每个名字是否指向"
    "原文中真实存在的人物。\n"
    "规则:\n"
    "1. 名字虽无字面匹配、但明显指向原文真实人物(别名、误写、称谓变体、"
    "由上下文可唯一确定的人物)时,判 is_real=true。\n"
    "2. 名字在原文中毫无依据(纯幻觉、张冠李戴、原文不存在的人物)时,"
    "判 is_real=false。\n"
    "3. confidence: high=确凿,medium=较有把握,low=拿不准;拿不准一律 low。\n"
    "4. 每个名字给出简短 reason。只输出 JSON,不要输出多余文本。"
)

_VERDICT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "is_real": {"type": "boolean"},
                    "confidence": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["name", "is_real", "confidence", "reason"],
            },
        }
    },
    "required": ["verdicts"],
}


def find_hallucination_candidates(
    fact: ChapterFact,
    chapter_text: str,
    protected_names: set[str] | None = None,
) -> list[str]:
    """挑选疑似幻觉人物候选:名字在原文中找不到、且不在白名单中的人物。

    "原文找不到" 复用证据锚定的 span 定位口径(归一化空白后子串匹配);
    "X·樵夫" 类消歧名按 "·" 后的基本名再查一次。
    """
    from src.extraction.chapter_fact_extractor import span_located

    protected = protected_names or set()
    candidates: list[str] = []
    for ch in fact.characters:
        name = ch.name
        if not name or name in protected:
            continue
        if span_located(name, chapter_text):
            continue
        base = name.split("·")[-1]
        if base != name and span_located(base, chapter_text):
            continue
        candidates.append(name)
    return candidates


def find_alias_candidates(
    fact: ChapterFact,
    chapter_text: str,
    protected_names: set[str] | None = None,
) -> dict[str, list[str]]:
    """挑选疑似幻觉别名候选:{宿主角色名: [原文不可定位的别名, ...]}。

    与人物候选同口径(span 定位 + 白名单);别名链此前零校验,
    LLM 编造的别名声明(如"神秘人是 X 的别名")由此进入送审范围。
    """
    from src.extraction.chapter_fact_extractor import span_located

    protected = protected_names or set()
    candidates: dict[str, list[str]] = {}
    for ch in fact.characters:
        for alias in (ch.new_aliases or []):
            if not alias or alias == ch.name or alias in protected:
                continue
            if span_located(alias, chapter_text):
                continue
            candidates.setdefault(ch.name, []).append(alias)
    return candidates


def build_review_prompt(candidates: list[str], chapter_text: str) -> str:
    """构造判定 user prompt:候选名单 + 章节原文。"""
    lines = ["## 疑似幻觉人物候选\n"]
    for name in candidates:
        lines.append(f"- {name}")
    lines.append("\n## 章节原文\n")
    lines.append(chapter_text)
    lines.append(
        '\n按 JSON 输出: {"verdicts": [{"name": "...", "is_real": true/false, '
        '"confidence": "high/medium/low", "reason": "..."}]},必须覆盖每个候选名。'
    )
    return "\n".join(lines)


def parse_verdicts(result: object, candidates: list[str]) -> dict[str, dict]:
    """解析 LLM 裁决为 {name: verdict};候选集之外的裁决忽略并记日志。"""
    candidate_set = set(candidates)
    if isinstance(result, dict):
        items = result.get("verdicts", [])
    elif isinstance(result, list):
        items = result
    else:
        return {}

    verdicts: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or name not in candidate_set:
            logger.warning("幻觉判定返回候选集之外的名字 %r,已忽略", name)
            continue
        confidence = item.get("confidence")
        if confidence not in _VALID_CONFIDENCE:
            confidence = "low"  # 非法置信度一律按拿不准处理(保守,不剔除)
        verdicts[name] = {
            "is_real": bool(item.get("is_real")),
            "confidence": confidence,
            "reason": str(item.get("reason") or ""),
        }
    return verdicts


def apply_verdicts(
    fact: ChapterFact,
    candidates: list[str],
    verdicts: dict[str, dict],
) -> tuple[ChapterFact, list[dict]]:
    """按裁决处置人物,返回 (新 ChapterFact, actions)。

    - is_real=true            → confirmed(保留);
    - is_real=false + high    → removed(剔除,并级联清理关系/事件参与者等引用);
    - is_real=false + 非 high → suspect(降级为存疑:保留,仅审计标记);
    - LLM 未覆盖的候选        → unjudged(保留)。
    """
    actions: list[dict] = []
    removed: set[str] = set()
    for name in candidates:
        verdict = verdicts.get(name)
        if verdict is None:
            actions.append({"name": name, "action": "unjudged",
                            "is_real": None, "confidence": None, "reason": ""})
            continue
        if verdict["is_real"]:
            action = "confirmed"
        elif verdict["confidence"] == "high":
            action = "removed"
            removed.add(name)
        else:
            action = "suspect"
        actions.append({"name": name, "action": action, **verdict})

    if not removed:
        return fact, actions

    characters = [ch for ch in fact.characters if ch.name not in removed]
    relationships = [
        rel for rel in fact.relationships
        if rel.person_a not in removed and rel.person_b not in removed
    ]
    events = [
        ev.model_copy(update={
            "participants": [p for p in ev.participants if p not in removed],
        })
        if any(p in removed for p in ev.participants) else ev
        for ev in fact.events
    ]
    item_events = [
        ie.model_copy(update={
            **({"actor": None} if ie.actor in removed else {}),
            **({"recipient": None} if ie.recipient in removed else {}),
        })
        if ie.actor in removed or ie.recipient in removed else ie
        for ie in fact.item_events
    ]
    org_events = [
        oe.model_copy(update={"member": None})
        if oe.member in removed else oe
        for oe in fact.org_events
    ]
    logger.info(
        "幻觉人物 LLM 层剔除 %d 个高置信幻觉: %s",
        len(removed), ", ".join(sorted(removed)),
    )
    return fact.model_copy(update={
        "characters": characters,
        "relationships": relationships,
        "events": events,
        "item_events": item_events,
        "org_events": org_events,
    }), actions


def apply_alias_verdicts(
    fact: ChapterFact,
    alias_candidates: dict[str, list[str]],
    verdicts: dict[str, dict],
) -> tuple[ChapterFact, list[dict]]:
    """按裁决处置别名,返回 (新 ChapterFact, actions)。

    与人物裁决同路径:仅 is_real=false + high 才剔除;剔除 = 从宿主角色的
    new_aliases 列表移除,不动 character 本身。低置信标 alias_suspect(保留),
    LLM 未覆盖的候选 alias_unjudged(保留)。
    """
    actions: list[dict] = []
    removed: dict[str, set[str]] = {}  # 宿主角色名 → 待剔除别名集合
    for owner, aliases in alias_candidates.items():
        for alias in aliases:
            verdict = verdicts.get(alias)
            if verdict is None:
                actions.append({"name": alias, "owner": owner,
                                "action": "alias_unjudged",
                                "is_real": None, "confidence": None, "reason": ""})
                continue
            if verdict["is_real"]:
                action = "alias_confirmed"
            elif verdict["confidence"] == "high":
                action = "alias_removed"
                removed.setdefault(owner, set()).add(alias)
            else:
                action = "alias_suspect"
            actions.append({"name": alias, "owner": owner, "action": action, **verdict})

    if not removed:
        return fact, actions

    characters = []
    for ch in fact.characters:
        drop = removed.get(ch.name)
        if drop:
            characters.append(ch.model_copy(update={
                "new_aliases": [a for a in ch.new_aliases if a not in drop],
            }))
        else:
            characters.append(ch)
    logger.info(
        "幻觉别名 LLM 层剔除 %d 条高置信幻觉别名: %s",
        sum(len(v) for v in removed.values()),
        ", ".join(f"{a}({o})" for o, aliases in removed.items() for a in aliases),
    )
    return fact.model_copy(update={"characters": characters}), actions


async def review_chapter_characters(
    fact: ChapterFact,
    *,
    chapter_text: str,
    llm: Any,
    novel_id: str = "",
    chapter_id: int | None = None,
    protected_names: set[str] | None = None,
    log_path: Path | None = None,
    record_cost: bool = True,
) -> ChapterFact:
    """幻觉人物 LLM 判定层主入口 (FR-4.2)。

    规则层之后、落库之前调用。仅当存在疑似候选时才发起 1 次轻量 LLM 调用;
    决策落 JSONL 审计日志 (NFR-5);任何失败都只记日志,原样返回 fact。
    """
    if not config.HALLUCINATION_REVIEW_ENABLED:
        return fact

    candidates = find_hallucination_candidates(fact, chapter_text, protected_names)
    alias_candidates = find_alias_candidates(fact, chapter_text, protected_names)
    # 别名一并送审(canonical 污染防线):与人物候选合并进同一次 LLM 调用,
    # 单章仍 ≤1 次轻量调用 (NFR-2)。
    alias_flat = [a for aliases in alias_candidates.values() for a in aliases]
    llm_candidates = list(dict.fromkeys(candidates + alias_flat))
    if not llm_candidates:
        return fact

    from src.infra.context_budget import get_budget
    from src.services.entity_resolver import _record_llm_cost, write_decision_log

    try:
        budget = get_budget()
        # 判定只需上下文依据,按章节截断预算封顶,控制 token 成本 (NFR-2)
        text = chapter_text[: budget.max_chapter_len]
        prompt = build_review_prompt(llm_candidates, text)
        result, usage = await llm.generate(
            _SYSTEM_PROMPT, prompt, format=_VERDICT_SCHEMA,
        )
        if record_cost:
            await _record_llm_cost(usage)
    except Exception as err:
        logger.warning(
            "Chapter %s: 幻觉人物判定调用失败(不影响既有结果): %s",
            chapter_id, err,
        )
        return fact

    verdicts = parse_verdicts(result, llm_candidates)
    new_fact, actions = apply_verdicts(fact, candidates, verdicts)
    new_fact, alias_actions = apply_alias_verdicts(
        new_fact, alias_candidates, verdicts,
    )

    write_decision_log(
        {
            "prompt_version": PROMPT_VERSION,
            "novel_id": novel_id,
            "chapter_id": chapter_id,
            "candidates": candidates,
            "alias_candidates": alias_candidates,
            "llm_raw_verdicts": result,
            "actions": actions + alias_actions,
        },
        log_path or AUDIT_LOG_PATH,
    )
    return new_fact

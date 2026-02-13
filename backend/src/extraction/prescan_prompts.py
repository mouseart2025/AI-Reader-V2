"""Prompt templates for entity pre-scan Phase 2 LLM classification."""

from __future__ import annotations

from src.models.entity_dict import EntityDictEntry

_SYSTEM_PROMPT = """\
你是一个小说实体分类专家。你的任务是对从一部小说中自动提取的高频专有名词候选列表进行分类。

## 任务
1. 对每个候选词判断其实体类型：person（人物）、location（地点）、item（物品/功法/武器）、org（组织/门派）、concept（概念/境界）
2. 识别可能的别名关系（同一实体的不同称呼）
3. 将明显不是实体的词标记为 rejected

## 输出格式
输出严格的 JSON，包含三个字段：
{
  "entities": [
    {"name": "韩立", "type": "person", "confidence": "high"},
    {"name": "七玄门", "type": "org", "confidence": "high"}
  ],
  "alias_groups": [
    ["孙悟空", "行者", "齐天大圣", "猴王"],
    ["唐僧", "三藏", "唐三藏", "御弟"]
  ],
  "rejected": ["然后", "不过", "自己"]
}

## 注意事项
- entities 中每个词只出现一次
- alias_groups 中的词必须是 entities 中已有的词
- 不确定的词宁可保留（标为 unknown），不要轻易 reject
- confidence: high（确定是实体）、medium（可能是实体）、low（不确定）
- 只输出 JSON，不要输出其他内容"""


def build_classification_prompt(
    candidates: list[EntityDictEntry],
    max_candidates: int = 150,
) -> tuple[str, str]:
    """Build system + user prompt for LLM entity classification.

    Returns (system_prompt, user_prompt).
    """
    # Take top candidates by frequency
    top = candidates[:max_candidates]

    lines = ["以下是从小说中自动提取的高频专有名词候选列表，请进行分类：", ""]
    lines.append("| 词 | 出现频次 | 来源 | 上下文示例 |")
    lines.append("|---|---------|------|----------|")

    for entry in top:
        ctx = (entry.sample_context or "")[:50]
        # Escape pipe characters in context
        ctx = ctx.replace("|", "\\|")
        lines.append(f"| {entry.name} | {entry.frequency} | {entry.source} | {ctx} |")

    user_prompt = "\n".join(lines)
    return _SYSTEM_PROMPT, user_prompt

"""内容生成 — 多平台适配文案 × 多叙事角度"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from marketing.config import get_config
from marketing.db import (
    get_content_item,
    get_content_items_by_status,
    get_db,
    update_content_item,
)
from marketing.llm_client import LLMClient
from marketing.logger import get_logger

log = get_logger("generator")

# ── 叙事角度 ───────────────────────────────────────────

NARRATIVE_ANGLES = {
    "visual": {
        "name": "震撼图谱",
        "instruction": (
            "用视觉冲击的方式展示AI生成的人物关系图谱，"
            "强调图谱的复杂度和美感，让读者感叹'WOW'。"
            "标题参考：'AI 自动生成的《{title}》关系图谱，看完头皮发麻'"
        ),
    },
    "pain": {
        "name": "读者痛点",
        "instruction": (
            "从读者追长篇小说的痛点出发（分不清人物、忘记关系），"
            "引出AI分析工具作为解决方案。"
            "标题参考：'追了N章还分不清谁是谁？AI帮你一键理清'"
        ),
    },
    "trivia": {
        "name": "冷知识",
        "instruction": (
            "从AI分析数据中提炼有趣的冷知识和意外发现，"
            "用数据说话，引发好奇心。"
            "标题参考：'《{title}》里出现最多的不是主角，而是...'"
        ),
    },
    "contrast": {
        "name": "对比吐槽",
        "instruction": (
            "用幽默的方式对比分析结果和读者直觉，"
            "制造反差和笑点。"
            "标题参考：'用AI分析《{title}》，发现主角的社交圈比我还小'"
        ),
    },
    "quiz": {
        "name": "互动问答",
        "instruction": (
            "设计互动测试或问答，引导读者参与评论，"
            "提高互动率。"
            "标题参考：'看关系图能认出几个角色？全认出来算你厉害'"
        ),
    },
}

# ── 平台风格 ───────────────────────────────────────────

PLATFORM_STYLES = {
    "xiaohongshu": (
        "用小红书爆款文案风格：emoji开头，口语化表达，分段短句，"
        "末尾加3-5个相关标签（#xxx#格式）。字数300-500字。"
    ),
    "juejin": (
        "用掘金技术社区风格：开头一句话总结，正文分析技术实现和效果，"
        "提到AI、LLM、知识图谱等技术关键词，末尾加技术标签。字数500-800字。"
    ),
    "zhihu": (
        "用知乎深度回答风格：先给结论，再展开分析，"
        "引用具体数据佐证，逻辑严谨有深度。字数800-1200字。"
    ),
    "twitter": (
        "用Twitter/X英文风格：简短有力的英文，1-2句核心观点 + 数据，"
        "3-5个hashtag。总长度≤280字符。"
    ),
}


# ── 分析数据获取 ───────────────────────────────────────


async def fetch_analysis_summary(
    novel_id: int,
    base_url: str = "http://localhost:8000",
) -> dict[str, Any]:
    """从 AI Reader API 获取小说分析摘要"""
    summary: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=15) as client:
        # 图谱数据 — 角色数和关系数
        try:
            resp = await client.get(
                f"{base_url}/api/novels/{novel_id}/visualization/graph"
            )
            if resp.status_code == 200:
                graph = resp.json()
                nodes = graph.get("nodes", [])
                edges = graph.get("edges", [])
                summary["character_count"] = len(nodes)
                summary["relation_count"] = len(edges)
                # 前 5 个关键角色
                top_chars = sorted(
                    nodes, key=lambda n: n.get("weight", 0), reverse=True
                )[:5]
                summary["top_characters"] = [
                    n.get("name", "") for n in top_chars
                ]
        except Exception as e:
            log.warning("获取图谱数据失败: %s", e)

        # 小说基本信息
        try:
            resp = await client.get(f"{base_url}/api/novels/{novel_id}")
            if resp.status_code == 200:
                novel = resp.json()
                summary["title"] = novel.get("title", "")
                summary["total_chapters"] = novel.get("total_chapters", 0)
                summary["total_words"] = novel.get("total_words", 0)
        except Exception as e:
            log.warning("获取小说信息失败: %s", e)

    return summary


# ── 文案生成 ───────────────────────────────────────────


def _build_prompt(
    summary: dict[str, Any],
    angle_key: str,
    platform: str,
    title: str,
) -> str:
    """构建文案生成 prompt"""
    angle = NARRATIVE_ANGLES[angle_key]
    style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["xiaohongshu"])

    context_parts = [f"小说《{title}》的 AI 分析数据："]
    if summary.get("character_count"):
        context_parts.append(f"- 角色数量: {summary['character_count']}")
    if summary.get("relation_count"):
        context_parts.append(f"- 关系数量: {summary['relation_count']}")
    if summary.get("top_characters"):
        context_parts.append(
            f"- 关键角色: {', '.join(summary['top_characters'])}"
        )
    if summary.get("total_chapters"):
        context_parts.append(f"- 总章节数: {summary['total_chapters']}")
    if summary.get("total_words"):
        context_parts.append(f"- 总字数: {summary['total_words']:,}")

    context = "\n".join(context_parts)
    angle_instruction = angle["instruction"].replace("{title}", title)

    return (
        f"你是一个顶级内容营销文案写手。\n\n"
        f"## 数据背景\n{context}\n\n"
        f"## 叙事角度: {angle['name']}\n{angle_instruction}\n\n"
        f"## 平台风格要求\n{style}\n\n"
        f"## 输出要求\n"
        f"请生成一篇营销文案，推广 AI Reader 这款免费开源的小说分析工具。\n"
        f"文案需要展示AI分析《{title}》的效果，引导读者下载使用。\n"
        f"官网: ai-reader.cc，GitHub开源免费。\n\n"
        f"## 严格约束（必须遵守）\n"
        f"1. 绝对不要编造具体数字（如对话字数、妖怪数量、对话占比等），只使用上面「数据背景」中提供的数据\n"
        f"2. 不要夸大分析速度，AI分析一本小说需要数小时逐章提取，不是「几秒」或「一键秒出」\n"
        f"3. 不要编造AI Reader不具备的功能，它的核心功能是：人物关系图谱、世界地图、时间线、百科、势力图、智能问答\n"
        f"4. 可以用夸张修辞表达感受，但具体数据必须真实或来自上述数据背景\n\n"
        f"输出JSON格式:\n"
        f'{{"title": "文案标题", "body": "正文内容", "tags": ["标签1", "标签2"]}}'
    )


async def generate_one(
    content_id: int,
    title: str,
    novel_id: int,
    angle: str,
    platform: str,
    output_dir: Path,
) -> dict[str, Any] | None:
    """生成单篇文案"""
    cfg = get_config()
    base_url = cfg.get("ai_reader", {}).get("base_url", "http://localhost:8000")

    # 获取分析摘要
    summary = await fetch_analysis_summary(novel_id, base_url)
    if not summary:
        summary = {"title": title}

    # 构建 prompt
    prompt = _build_prompt(summary, angle, platform, title)

    # 调用 LLM
    client = LLMClient(role="copywriting")
    resp = await client.chat(
        [{"role": "user", "content": prompt}],
        temperature=0.8,
    )

    # 解析结果（处理 markdown 代码块包裹）
    raw = resp.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        content = json.loads(raw)
    except json.JSONDecodeError:
        log.error("LLM 返回非 JSON: %s", raw[:200])
        return None

    # 保存文案文件
    content_dir = output_dir / "content"
    content_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{angle}_{platform}.md"
    filepath = content_dir / filename

    md_text = f"# {content.get('title', '')}\n\n{content.get('body', '')}"
    if content.get("tags"):
        md_text += f"\n\n---\nTags: {', '.join(content['tags'])}"

    filepath.write_text(md_text, encoding="utf-8")

    result = {
        "angle": angle,
        "platform": platform,
        "title": content.get("title", ""),
        "body": content.get("body", ""),
        "tags": content.get("tags", []),
        "file": str(filepath),
        "tokens": resp.input_tokens + resp.output_tokens,
        "cost_yuan": resp.cost_yuan,
    }

    log.info(
        "文案生成: [%s/%s] %s (tokens: %d, ¥%.4f)",
        angle, platform, content.get("title", "")[:30],
        resp.input_tokens + resp.output_tokens, resp.cost_yuan,
    )

    return result


def _pick_angles(
    used_angles: list[str],
    requested: str | None = None,
    count: int = 5,
) -> list[str]:
    """选择叙事角度，优先未使用的"""
    all_angles = list(NARRATIVE_ANGLES.keys())

    if requested and requested in all_angles:
        return [requested]

    # 优先未使用的角度
    unused = [a for a in all_angles if a not in used_angles]
    if len(unused) >= count:
        return unused[:count]

    # 不够则轮换
    return (unused + [a for a in all_angles if a in used_angles])[:count]


async def generate_for_content(
    content_id: int,
    platform: str | None = None,
    angle: str | None = None,
) -> bool:
    """为一个内容项生成文案"""
    db = await get_db()
    item = await get_content_item(db, content_id)
    if not item:
        log.error("content_id=%d 不存在", content_id)
        return False

    title = item["novel_title"]
    step_outputs = json.loads(item.get("step_outputs", "{}"))
    novel_id = step_outputs.get("analysis", {}).get("novel_id") or item.get("novel_id")

    if not novel_id:
        log.error("content_id=%d 缺少 novel_id", content_id)
        return False

    # 确定平台列表
    platforms = [platform] if platform else ["xiaohongshu"]

    # 确定角度
    used = step_outputs.get("content", {}).get("used_angles", [])
    angles = _pick_angles(used, angle)

    cfg = get_config()
    output_base = Path(cfg.get("output", {}).get("dir", "./output"))
    output_dir = output_base / f"content-{content_id}"

    total_tokens = 0
    total_cost = 0.0
    generated: list[dict[str, Any]] = []

    for a in angles:
        for p in platforms:
            result = await generate_one(
                content_id, title, novel_id, a, p, output_dir,
            )
            if result:
                generated.append(result)
                total_tokens += result["tokens"]
                total_cost += result["cost_yuan"]

    if not generated:
        log.error("《%s》文案生成全部失败", title)
        return False

    # 更新状态
    new_used = used + [g["angle"] for g in generated]
    await update_content_item(
        db, content_id,
        status="content_ready",
        narrative_angle=",".join(set(g["angle"] for g in generated)),
        platform=",".join(set(g["platform"] for g in generated)),
        llm_tokens_used=(item.get("llm_tokens_used", 0) or 0) + total_tokens,
        llm_cost_yuan=(item.get("llm_cost_yuan", 0.0) or 0.0) + total_cost,
        step_outputs={
            "content": {
                "articles": [
                    {k: v for k, v in g.items() if k != "body"}
                    for g in generated
                ],
                "used_angles": list(set(new_used)),
                "total_cost_yuan": total_cost,
            },
        },
    )

    log.info(
        "《%s》文案生成完成: %d 篇, tokens=%d, ¥%.4f",
        title, len(generated), total_tokens, total_cost,
    )
    return True


async def run_generator(
    platform: str | None = None,
    angle: str | None = None,
) -> None:
    """文案生成入口"""
    db = await get_db()

    # 优先处理有截图的，降级处理已分析的
    items = await get_content_items_by_status(db, "screenshots_ready")
    if not items:
        items = await get_content_items_by_status(db, "analyzed")

    log.info("发现 %d 个待生成文案的内容", len(items))

    if not items:
        print("没有待生成文案的内容（需要先完成分析或截图）")
        return

    success = 0
    for item in items:
        ok = await generate_for_content(item["id"], platform, angle)
        if ok:
            success += 1

    print(f"\n文案生成完成: {success}/{len(items)} 成功")

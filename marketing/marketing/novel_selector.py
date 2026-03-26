"""小说选题 — 热门小说发现与 LLM 图谱适配度评估"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from marketing.config import get_config
from marketing.db import create_content_item, get_db, is_novel_selected
from marketing.llm_client import LLMClient
from marketing.logger import get_logger

log = get_logger("selector")

_QIDIAN_RANK_URL = "https://www.qidian.com/rank/yuepiao/"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


@dataclass
class NovelCandidate:
    title: str
    author: str
    genre: str
    synopsis: str
    word_count: str
    rank: int
    source: str = "manual"


@dataclass
class NovelEvaluation:
    title: str
    score: float
    reason: str
    char_complexity: float
    visual_impact: float
    topic_heat: float
    already_selected: bool = False


# ── 榜单抓取 ───────────────────────────────────────────


async def fetch_qidian_rank(top: int = 20) -> list[NovelCandidate]:
    """尝试抓取起点月票榜，失败则返回空列表"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                _QIDIAN_RANK_URL,
                headers={"User-Agent": _UA},
                follow_redirects=True,
            )
            resp.raise_for_status()
            return _parse_qidian_html(resp.text, top)
    except Exception as e:
        log.warning("起点榜单抓取失败: %s — 请使用手动输入模式", e)
        return []


def _parse_qidian_html(html: str, top: int) -> list[NovelCandidate]:
    """从起点榜单页面提取小说信息（基础正则解析）"""
    import re

    novels: list[NovelCandidate] = []

    # 起点页面结构可能变化，用宽泛的正则尽力提取
    # 书名: <a ... data-bid="xxx">书名</a>  or <h2><a>书名</a></h2>
    title_pattern = re.compile(
        r'class="book-mid-info".*?<a[^>]*>([^<]+)</a>.*?'
        r'<p class="author">.*?<a[^>]*>([^<]+)</a>.*?'
        r'<a[^>]*>([^<]+)</a>.*?'
        r'<p class="intro">([^<]*)</p>',
        re.DOTALL,
    )

    for i, m in enumerate(title_pattern.finditer(html)):
        if i >= top:
            break
        novels.append(NovelCandidate(
            title=m.group(1).strip(),
            author=m.group(2).strip(),
            genre=m.group(3).strip(),
            synopsis=m.group(4).strip(),
            word_count="",
            rank=i + 1,
            source="qidian",
        ))

    if not novels:
        log.warning("未能从页面解析出小说数据，HTML 结构可能已变更")

    return novels


def parse_manual_input(text: str) -> list[NovelCandidate]:
    """解析手动输入的小说列表（每行一个：书名 | 作者 | 类型 | 简介）"""
    novels: list[NovelCandidate] = []
    for i, line in enumerate(text.strip().splitlines(), 1):
        parts = [p.strip() for p in line.split("|")]
        if not parts or not parts[0]:
            continue
        novels.append(NovelCandidate(
            title=parts[0],
            author=parts[1] if len(parts) > 1 else "未知",
            genre=parts[2] if len(parts) > 2 else "未知",
            synopsis=parts[3] if len(parts) > 3 else "",
            word_count=parts[4] if len(parts) > 4 else "",
            rank=i,
            source="manual",
        ))
    return novels


# ── LLM 评估 ───────────────────────────────────────────

_EVAL_PROMPT = """你是一个内容营销专家。以下是一部小说的信息，请评估它是否适合制作"人物关系图谱"类内容用于社交媒体传播。

小说: {title}
作者: {author}
类型: {genre}
简介: {synopsis}
字数: {word_count}

请从以下维度评分(1-10)并给出理由:
1. 角色复杂度: 主要角色数量和关系密度
2. 视觉冲击力: 图谱生成后的视觉效果预估
3. 话题性: 在社交媒体上的讨论热度和受众范围
4. 综合评分: 前三项的加权平均(角色复杂度×0.4 + 视觉冲击力×0.3 + 话题性×0.3)

严格输出JSON格式:
{{"score": 8, "reason": "一句话推荐理由", "char_complexity": 9, "visual_impact": 7, "topic_heat": 8}}"""


async def evaluate_novels(
    novels: list[NovelCandidate],
    top: int = 5,
) -> list[NovelEvaluation]:
    """使用 LLM 批量评估小说的图谱适配度"""
    if not novels:
        return []

    client = LLMClient(role="analysis")
    db = await get_db()

    # 构建批量评估（一次调用评估所有小说，节省成本）
    batch_prompt = "请逐一评估以下小说的图谱适配度，每本输出一个JSON对象。\n\n"
    for n in novels:
        batch_prompt += f"### {n.rank}. {n.title}\n"
        batch_prompt += f"作者: {n.author} | 类型: {n.genre}\n"
        batch_prompt += f"简介: {n.synopsis or '暂无'}\n"
        batch_prompt += f"字数: {n.word_count or '未知'}\n\n"

    batch_prompt += (
        "\n对每本小说，评估其制作人物关系图谱内容的潜力，从以下维度评分(1-10):\n"
        "- char_complexity: 角色复杂度（角色数量、关系密度）\n"
        "- visual_impact: 视觉冲击力（图谱生成后的视觉效果）\n"
        "- topic_heat: 话题性（社交媒体讨论热度和受众范围）\n"
        "- score: 综合评分（加权平均）\n"
        "- reason: 一句话推荐理由\n\n"
        "严格输出JSON数组格式:\n"
        '[{"title": "书名", "score": 8, "reason": "...", '
        '"char_complexity": 9, "visual_impact": 7, "topic_heat": 8}, ...]'
    )

    log.info("正在评估 %d 部小说的图谱适配度...", len(novels))

    resp = await client.chat(
        [{"role": "user", "content": batch_prompt}],
        temperature=0.3,
        max_tokens=16384,
    )

    # 解析 LLM 返回（处理 markdown 代码块包裹）
    evaluations: list[NovelEvaluation] = []
    try:
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        # 可能是数组或包裹在某个 key 下
        items = data if isinstance(data, list) else data.get("novels", data.get("results", []))

        for item in items:
            title = item.get("title", "")
            already = await is_novel_selected(db, title)
            evaluations.append(NovelEvaluation(
                title=title,
                score=float(item.get("score", 0)),
                reason=item.get("reason", ""),
                char_complexity=float(item.get("char_complexity", 0)),
                visual_impact=float(item.get("visual_impact", 0)),
                topic_heat=float(item.get("topic_heat", 0)),
                already_selected=already,
            ))
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.error("LLM 返回解析失败: %s\n原始内容: %s", e, resp.content[:500])
        return []

    # 按综合评分排序
    evaluations.sort(key=lambda e: e.score, reverse=True)
    log.info(
        "评估完成 (tokens: %d+%d, 费用: ¥%.4f)",
        resp.input_tokens, resp.output_tokens, resp.cost_yuan,
    )

    return evaluations[:top]


# ── 选题确认 ───────────────────────────────────────────


async def confirm_selection(
    evaluations: list[NovelEvaluation],
    pick_index: int,
) -> int | None:
    """确认选题，写入 content_items 表，返回 content_id"""
    if pick_index < 1 or pick_index > len(evaluations):
        log.error("无效的选择: %d (可选范围 1-%d)", pick_index, len(evaluations))
        return None

    chosen = evaluations[pick_index - 1]

    if chosen.already_selected:
        log.warning("《%s》已被选过，如需重新选题请先在数据库中清除", chosen.title)
        return None

    db = await get_db()
    content_id = await create_content_item(
        db,
        novel_title=chosen.title,
        status="selected",
    )

    log.info("选题确认: 《%s》 → content_id=%d", chosen.title, content_id)

    # 提示 TXT 文件准备
    _prompt_txt_location(chosen.title)
    return content_id


def _prompt_txt_location(title: str) -> None:
    """提示操作者准备 TXT 文件"""
    cfg = get_config()
    novels_dir = cfg.get("novels_dir", "./novels")

    # 尝试模糊匹配本地文件
    novels_path = Path(novels_dir)
    if novels_path.exists():
        matches = list(novels_path.glob(f"*{title}*.txt"))
        if matches:
            print(f"\n📚 本地已找到匹配文件: {matches[0]}")
            return

    print(f"\n📋 下一步: 请准备《{title}》的 TXT 文件")
    print(f"   放置到: {novels_dir}/{title}.txt")
    print(f"   然后运行: python -m marketing analyze")


# ── 终端输出 ───────────────────────────────────────────


def print_evaluations(evaluations: list[NovelEvaluation]) -> None:
    """格式化输出评估结果"""
    if not evaluations:
        print("\n⚠️  没有评估结果")
        return

    print(f"\n{'='*70}")
    print("📊 图谱适配度评估结果")
    print(f"{'='*70}")
    print(f"{'#':>2}  {'综合':>4}  {'角色':>4}  {'视觉':>4}  {'话题':>4}  {'书名':<20}  {'理由'}")
    print(f"{'-'*70}")

    for i, ev in enumerate(evaluations, 1):
        selected_mark = " [已选]" if ev.already_selected else ""
        print(
            f"{i:>2}  {ev.score:>4.1f}  {ev.char_complexity:>4.1f}  "
            f"{ev.visual_impact:>4.1f}  {ev.topic_heat:>4.1f}  "
            f"{ev.title:<20s}  {ev.reason}{selected_mark}"
        )

    print(f"\n💡 确认选题: python -m marketing pick --confirm N")


# ── CLI 入口 ───────────────────────────────────────────


async def run_selector(top: int = 5, confirm: int | None = None) -> None:
    """选题主入口"""

    # 模式 1: 确认选题
    if confirm is not None:
        # 需要先有评估结果 — 重新运行评估或从缓存获取
        log.info("确认选题 #%d — 需要先获取候选列表", confirm)
        candidates = await _get_candidates()
        if not candidates:
            print("⚠️  没有候选小说。请先运行 `python -m marketing pick` 获取推荐")
            return
        evaluations = await evaluate_novels(candidates, top=20)
        await confirm_selection(evaluations, confirm)
        return

    # 模式 2: 获取推荐
    candidates = await _get_candidates()
    if not candidates:
        print("\n⚠️  未获取到候选小说")
        print("💡 你可以手动输入小说列表，格式: 书名 | 作者 | 类型 | 简介")
        print("   保存为 novels_input.txt 后配置到 config.yaml 的 manual_novels_file")
        return

    evaluations = await evaluate_novels(candidates, top=top)
    print_evaluations(evaluations)


async def _get_candidates() -> list[NovelCandidate]:
    """获取候选小说列表（自动抓取 or 手动输入文件）"""
    cfg = get_config()

    # 优先检查手动输入文件
    manual_file = cfg.get("manual_novels_file")
    if manual_file:
        path = Path(manual_file)
        if path.exists():
            text = path.read_text(encoding="utf-8")
            candidates = parse_manual_input(text)
            if candidates:
                log.info("从手动输入文件加载 %d 部小说", len(candidates))
                return candidates

    # 尝试自动抓取
    log.info("尝试抓取起点月票榜...")
    candidates = await fetch_qidian_rank(top=20)

    if not candidates:
        log.info("自动抓取失败，尝试读取默认输入文件 novels_input.txt")
        fallback = Path("novels_input.txt")
        if fallback.exists():
            text = fallback.read_text(encoding="utf-8")
            candidates = parse_manual_input(text)

    return candidates

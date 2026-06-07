"""Human-readable per-chapter analysis export (Markdown).

Renders each chapter's ChapterFact as a clean, reading-oriented Markdown
document — an alternative to the raw JSON export, which users find hard to read
(GitHub issue #26, magik163). Names are resolved through the alias map so the
output matches what users see in the app and reflects their manual edits
(merge/split/rename overrides). Internal-only fields (spatial relationships,
world declarations) are omitted to keep it narrative-focused.
"""

from __future__ import annotations

import json
from datetime import datetime

from src.db.sqlite_db import get_connection
from src.models.chapter_fact import ChapterFact
from src.services.alias_resolver import build_alias_map


def _resolve(name: str, alias_map: dict[str, str]) -> str:
    return alias_map.get(name, name) if name else name


def _render_chapter(fact: ChapterFact, title: str, alias_map: dict[str, str]) -> str:
    out: list[str] = []
    heading = f"## 第 {fact.chapter_id} 章"
    if title:
        heading += f" {title}"
    out.append(heading)
    out.append("")

    if fact.characters:
        out.append("### 👥 人物")
        for c in fact.characters:
            name = _resolve(c.name, alias_map)
            aliases = [a for a in c.new_aliases if a and a != name]
            line = f"- **{name}**"
            if aliases:
                line += f"（别名：{ '、'.join(aliases) }）"
            if c.appearance:
                line += f" — {c.appearance}"
            out.append(line)
            for ab in c.abilities_gained:
                bits = [b for b in (ab.dimension, ab.name) if b]
                label = "·".join(bits)
                desc = f"：{ab.description}" if ab.description else ""
                if label or desc:
                    out.append(f"  - {label}{desc}")
        out.append("")

    if fact.relationships:
        out.append("### 🤝 关系")
        for r in fact.relationships:
            a = _resolve(r.person_a, alias_map)
            b = _resolve(r.person_b, alias_map)
            line = f"- {a} —（{r.relation_type}）→ {b}"
            if r.evidence:
                line += f"：{r.evidence}"
            out.append(line)
        out.append("")

    if fact.locations:
        out.append("### 📍 地点")
        for loc in fact.locations:
            name = _resolve(loc.name, alias_map)
            line = f"- **{name}**"
            if loc.type:
                line += f"（{loc.type}）"
            if loc.parent:
                line += f" · 属于 {_resolve(loc.parent, alias_map)}"
            if loc.description:
                line += f" — {loc.description}"
            out.append(line)
        out.append("")

    if fact.item_events:
        out.append("### 🎒 物品")
        for ie in fact.item_events:
            line = f"- **{ie.item_name}**"
            if ie.item_type:
                line += f"（{ie.item_type}）"
            if ie.action:
                line += f" · {ie.action}"
            actor = _resolve(ie.actor or "", alias_map)
            recipient = _resolve(ie.recipient or "", alias_map)
            if actor and recipient:
                line += f"：{actor} → {recipient}"
            elif actor:
                line += f"：{actor}"
            if ie.description:
                line += f" — {ie.description}"
            out.append(line)
        out.append("")

    if fact.org_events:
        out.append("### 🏛️ 组织")
        for oe in fact.org_events:
            line = f"- **{oe.org_name}**"
            if oe.org_type:
                line += f"（{oe.org_type}）"
            member = _resolve(oe.member or "", alias_map)
            if member:
                line += f"：{member}"
                if oe.role:
                    line += f"（{oe.role}）"
                if oe.action:
                    line += f" {oe.action}"
            if oe.description:
                line += f" — {oe.description}"
            if oe.org_relation:
                line += f" · 与 {oe.org_relation.other_org}：{oe.org_relation.type}"
            out.append(line)
        out.append("")

    if fact.events:
        out.append("### ⚡ 事件")
        _imp = {"high": "🔴", "medium": "🟡", "low": "⚪"}
        for ev in fact.events:
            badge = _imp.get(ev.importance, "")
            line = f"- {badge} {ev.summary}".rstrip()
            meta = []
            if ev.type:
                meta.append(ev.type)
            if ev.participants:
                meta.append("参与：" + "、".join(_resolve(p, alias_map) for p in ev.participants))
            if ev.location:
                meta.append("地点：" + _resolve(ev.location, alias_map))
            if meta:
                line += f"（{ '；'.join(meta) }）"
            out.append(line)
        out.append("")

    if fact.new_concepts:
        out.append("### 💡 新概念")
        for nc in fact.new_concepts:
            line = f"- **{nc.name}**"
            if nc.category:
                line += f"（{nc.category}）"
            if nc.definition:
                line += f"：{nc.definition}"
            out.append(line)
        out.append("")

    # No facts at all → note it so the chapter isn't a confusing blank.
    if len(out) <= 2:
        out.append("_（本章未抽取到结构化事实）_")
        out.append("")

    out.append("---")
    out.append("")
    return "\n".join(out)


async def render_novel_markdown(novel_id: str) -> tuple[str, str]:
    """Render a novel's full per-chapter analysis as readable Markdown.

    Returns (markdown_text, filename).
    """
    conn = await get_connection()
    try:
        cur = await conn.execute("SELECT title FROM novels WHERE id = ?", (novel_id,))
        row = await cur.fetchone()
        if not row:
            raise ValueError(f"Novel {novel_id} not found")
        novel_title = row["title"]

        cur = await conn.execute(
            "SELECT chapter_num, title FROM chapters WHERE novel_id = ? ORDER BY chapter_num",
            (novel_id,),
        )
        titles = {r["chapter_num"]: (r["title"] or "") for r in await cur.fetchall()}

        cur = await conn.execute(
            """SELECT c.chapter_num, cf.fact_json
               FROM chapter_facts cf
               JOIN chapters c ON c.id = cf.chapter_id AND c.novel_id = cf.novel_id
               WHERE cf.novel_id = ?
               ORDER BY c.chapter_num""",
            (novel_id,),
        )
        fact_rows = await cur.fetchall()
    finally:
        await conn.close()

    alias_map = await build_alias_map(novel_id)

    parts: list[str] = [
        f"# 《{novel_title}》分析报告",
        "",
        f"> 共 {len(fact_rows)} 章已分析 · 导出于 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "> 由 AI Reader 生成；名称已套用别名归并与手动修正。",
        "",
        "---",
        "",
    ]

    for r in fact_rows:
        ch_num = r["chapter_num"]
        try:
            data = json.loads(r["fact_json"])
            data["chapter_id"] = ch_num
            data["novel_id"] = novel_id
            fact = ChapterFact.model_validate(data)
        except Exception:
            continue
        parts.append(_render_chapter(fact, titles.get(ch_num, ""), alias_map))

    filename = f"{novel_title}_分析报告.md"
    return "\n".join(parts), filename

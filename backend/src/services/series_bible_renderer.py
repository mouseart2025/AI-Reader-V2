"""Series Bible Markdown renderer — convert collected data to .md content.

Supports two templates:
- "complete": 通用模板 — all modules, full detail
- "author": 网文作者套件 — character cards + org overview + timeline outline
"""

from __future__ import annotations

from src.services.series_bible_service import SeriesBibleData

# ── Template registry ────────────────────────────

TEMPLATES = {
    "complete": {
        "name": "通用模板",
        "description": "完整世界观文档，含全部模块",
        "default_modules": ["characters", "relations", "locations", "items", "orgs", "timeline"],
    },
    "author": {
        "name": "网文作者套件",
        "description": "人物设定卡 + 势力分布 + 时间线大纲",
        "default_modules": ["characters", "orgs", "timeline"],
    },
}

DEFAULT_TEMPLATE = "complete"


def get_template_info() -> list[dict]:
    """Return available templates for frontend display."""
    return [
        {"id": tid, "name": t["name"], "description": t["description"]}
        for tid, t in TEMPLATES.items()
    ]


def render_markdown(data: SeriesBibleData, template: str = "complete") -> str:
    """Render SeriesBibleData as a Markdown document using specified template."""
    tpl = TEMPLATES.get(template, TEMPLATES[DEFAULT_TEMPLATE])

    # Determine which modules to render: intersection of requested + template defaults
    if data.modules:
        active_modules = [m for m in data.modules if m in tpl["default_modules"]]
        # If user explicitly requested modules, use those even if not in template
        if not active_modules:
            active_modules = data.modules
    else:
        active_modules = tpl["default_modules"]

    lines: list[str] = []

    # Title
    title_suffix = tpl["name"]
    lines.append(f"# {data.novel_title} — {title_suffix}")
    if data.novel_author:
        lines.append(f"\n> 作者: {data.novel_author}")
    lines.append(f"\n> 分析范围: 第 {data.chapter_range[0]} ~ {data.chapter_range[1]} 章")
    lines.append("")

    # Table of contents
    lines.append("## 目录\n")
    toc_idx = 1
    toc_sections = _build_toc(data, active_modules)
    for label in toc_sections:
        anchor = label.replace(" ", "-")
        lines.append(f"{toc_idx}. [{label}](#{anchor})")
        toc_idx += 1
    lines.append("")

    # ── Characters ──────────────────────────────
    if "characters" in active_modules and data.characters:
        lines.append("---\n")
        lines.append("## 人物档案\n")
        if template == "author":
            _render_character_cards(lines, data.characters)
        else:
            _render_characters_full(lines, data.characters)

    # ── Relations ───────────────────────────────
    if "relations" in active_modules and data.relations:
        lines.append("---\n")
        lines.append("## 关系网络\n")
        _render_relations(lines, data.relations)

    # ── Locations ───────────────────────────────
    if "locations" in active_modules and data.locations:
        lines.append("---\n")
        lines.append("## 地点百科\n")
        _render_locations(lines, data.locations)

    # ── Items ───────────────────────────────────
    if "items" in active_modules and data.items:
        lines.append("---\n")
        lines.append("## 物品道具\n")
        _render_items(lines, data.items)

    # ── Orgs ────────────────────────────────────
    if "orgs" in active_modules and data.orgs:
        lines.append("---\n")
        if template == "author":
            lines.append("## 势力分布\n")
        else:
            lines.append("## 组织势力\n")
        _render_orgs(lines, data.orgs)

    # ── Timeline ────────────────────────────────
    if "timeline" in active_modules and data.timeline:
        lines.append("---\n")
        if template == "author":
            lines.append("## 时间线大纲\n")
            _render_timeline_outline(lines, data.timeline)
        else:
            lines.append("## 时间线\n")
            _render_timeline_full(lines, data.timeline)

    # Footer
    lines.append("---\n")
    lines.append("*由 AI Reader V2 自动生成*\n")

    return "\n".join(lines)


# ── Section renderers ────────────────────────────


def _build_toc(data: SeriesBibleData, modules: list[str]) -> list[str]:
    """Build table of contents labels."""
    sections = []
    if "characters" in modules and data.characters:
        sections.append("人物档案")
    if "relations" in modules and data.relations:
        sections.append("关系网络")
    if "locations" in modules and data.locations:
        sections.append("地点百科")
    if "items" in modules and data.items:
        sections.append("物品道具")
    if "orgs" in modules and data.orgs:
        sections.append("组织势力")
    if "timeline" in modules and data.timeline:
        sections.append("时间线")
    return sections


def _render_character_cards(lines: list[str], characters: list[dict]) -> None:
    """Author template: compact character setting cards."""
    for ch in characters:
        lines.append(f"### {ch['name']}\n")

        # Card format: key-value pairs
        aliases = ch.get("aliases", [])
        alias_names = [a["name"] for a in aliases if a["name"] != ch["name"]]
        if alias_names:
            lines.append(f"- **别称:** {', '.join(alias_names)}")

        appearances = ch.get("appearances", [])
        if appearances:
            desc = appearances[0].get("description", "")
            if desc:
                lines.append(f"- **外貌:** {desc}")

        abilities = ch.get("abilities", [])
        if abilities:
            ab_list = [f"{a.get('name', '')}" for a in abilities[:4]]
            lines.append(f"- **能力:** {', '.join(ab_list)}")

        relations = ch.get("relations", [])
        if relations:
            rel_parts = []
            for rel in relations[:5]:
                other = rel.get("other_person", "")
                stages = rel.get("stages", [])
                rel_type = stages[-1].get("relation_type", "") if stages else ""
                rel_parts.append(f"{other}({rel_type})" if rel_type else other)
            lines.append(f"- **关系:** {', '.join(rel_parts)}")

        experiences = ch.get("experiences", [])
        if experiences:
            exp_parts = [e.get("summary", "")[:30] for e in experiences[:3]]
            lines.append(f"- **经历:** {'；'.join(exp_parts)}")

        stats = ch.get("stats", {})
        lines.append(
            f"- **出场:** 第{stats.get('first_chapter', '?')}章起，共{stats.get('chapter_count', 0)}章"
        )
        lines.append("")


def _render_characters_full(lines: list[str], characters: list[dict]) -> None:
    """Complete template: detailed character profiles."""
    for ch in characters:
        lines.append(f"### {ch['name']}\n")

        aliases = ch.get("aliases", [])
        alias_names = [a["name"] for a in aliases if a["name"] != ch["name"]]
        if alias_names:
            lines.append(f"**别称:** {', '.join(alias_names)}\n")

        appearances = ch.get("appearances", [])
        if appearances:
            lines.append("**外貌特征:**\n")
            for ap in appearances[:3]:
                lines.append(f"- {ap['description']}")
            lines.append("")

        abilities = ch.get("abilities", [])
        if abilities:
            lines.append("**能力:**\n")
            for ab in abilities[:5]:
                dim = ab.get("dimension", "")
                name = ab.get("name", "")
                desc = ab.get("description", "")
                lines.append(f"- **{dim}·{name}**: {desc}")
            lines.append("")

        relations = ch.get("relations", [])
        if relations:
            lines.append("**人物关系:**\n")
            for rel in relations[:10]:
                other = rel.get("other_person", "")
                category = rel.get("category", "other")
                stages = rel.get("stages", [])
                if len(stages) > 1:
                    # Show evolution chain
                    chain = " → ".join(s.get("relation_type", "") for s in stages)
                    lines.append(f"- {other} — {chain} ({_cat_label(category)})")
                elif stages:
                    rel_type = stages[0].get("relation_type", "")
                    lines.append(f"- {other} — {rel_type} ({_cat_label(category)})")
                else:
                    lines.append(f"- {other}")
            lines.append("")

        experiences = ch.get("experiences", [])
        if experiences:
            lines.append("**主要经历:**\n")
            for exp in experiences[:8]:
                ch_num = exp.get("chapter", "")
                summary = exp.get("summary", "")
                loc = exp.get("location")
                loc_str = f" @ {loc}" if loc else ""
                lines.append(f"- [第{ch_num}章] {summary}{loc_str}")
            lines.append("")

        items = ch.get("items", [])
        if items:
            lines.append("**持有物品:**\n")
            for it in items[:5]:
                lines.append(
                    f"- {it.get('item_name', '')} — {it.get('action', '')} "
                    f"(第{it.get('chapter', '')}章)"
                )
            lines.append("")

        stats = ch.get("stats", {})
        if stats:
            lines.append(
                f"*出场章数: {stats.get('chapter_count', 0)} · "
                f"首次出场: 第{stats.get('first_chapter', '?')}章*\n"
            )


def _render_relations(lines: list[str], relations: dict) -> None:
    """Render relationship network as table."""
    edges = relations.get("edges", [])
    if edges:
        lines.append("| 人物A | 人物B | 关系 | 章节数 |")
        lines.append("|-------|-------|------|--------|")
        sorted_edges = sorted(edges, key=lambda e: e.get("weight", 0), reverse=True)
        for edge in sorted_edges[:30]:
            src = _escape_pipe(edge.get("source", ""))
            tgt = _escape_pipe(edge.get("target", ""))
            rel = _escape_pipe(edge.get("relation_type", ""))
            lines.append(f"| {src} | {tgt} | {rel} | {edge.get('weight', 0)} |")
        lines.append("")


def _render_locations(lines: list[str], locations: list[dict]) -> None:
    """Render location encyclopedia."""
    for loc in locations:
        lines.append(f"### {loc['name']}\n")

        loc_type = loc.get("location_type", "")
        parent = loc.get("parent")
        if loc_type or parent:
            meta = []
            if loc_type:
                meta.append(f"类型: {loc_type}")
            if parent:
                meta.append(f"上级: {parent}")
            lines.append(f"**{' · '.join(meta)}**\n")

        children = loc.get("children", [])
        if children:
            lines.append(f"**下级地点:** {', '.join(children[:10])}\n")

        descriptions = loc.get("descriptions", [])
        if descriptions:
            for desc in descriptions[:3]:
                lines.append(f"- {desc.get('description', '')}")
            lines.append("")

        visitors = loc.get("visitors", [])
        if visitors:
            visitor_names = [
                f"{v['name']}{'(常驻)' if v.get('is_resident') else ''}"
                for v in visitors[:8]
            ]
            lines.append(f"**到访者:** {', '.join(visitor_names)}\n")

        stats = loc.get("stats", {})
        if stats:
            lines.append(
                f"*提及章数: {stats.get('chapter_count', 0)} · "
                f"首次出现: 第{stats.get('first_chapter', '?')}章*\n"
            )


def _render_items(lines: list[str], items: list[dict]) -> None:
    """Render item catalog."""
    for item in items:
        lines.append(f"### {item['name']}\n")

        item_type = item.get("item_type", "")
        if item_type:
            lines.append(f"**类型:** {item_type}\n")

        flow = item.get("flow", [])
        if flow:
            lines.append("**流转记录:**\n")
            for f in flow[:8]:
                ch_num = f.get("chapter", "")
                action = f.get("action", "")
                actor = f.get("actor", "")
                desc = f.get("description", "")
                lines.append(f"- [第{ch_num}章] {actor} {action}")
                if desc:
                    lines.append(f"  {desc}")
            lines.append("")


def _render_orgs(lines: list[str], orgs: list[dict]) -> None:
    """Render organization / faction info."""
    for org in orgs:
        lines.append(f"### {org['name']}\n")

        org_type = org.get("org_type", "")
        if org_type:
            lines.append(f"**类型:** {org_type}\n")

        members = org.get("member_events", [])
        if members:
            lines.append("**成员变动:**\n")
            for m in members[:10]:
                ch_num = m.get("chapter", "")
                member = m.get("member", "")
                action = m.get("action", "")
                role = m.get("role", "")
                role_str = f" ({role})" if role else ""
                lines.append(f"- [第{ch_num}章] {member}{role_str} — {action}")
            lines.append("")

        org_rels = org.get("org_relations", [])
        if org_rels:
            lines.append("**组织关系:**\n")
            for r in org_rels[:5]:
                lines.append(
                    f"- {r.get('other_org', '')} — {r.get('relation_type', '')}"
                )
            lines.append("")


def _render_timeline_full(lines: list[str], events: list[dict]) -> None:
    """Complete template: full timeline with all events."""
    current_chapter = -1
    for ev in events[:100]:
        ch_num = ev.get("chapter", 0)
        if ch_num != current_chapter:
            current_chapter = ch_num
            lines.append(f"\n#### 第{ch_num}章\n")
        importance = ev.get("importance", "medium")
        marker = "**" if importance == "high" else ""
        summary = ev.get("summary", "")
        participants = ev.get("participants", [])
        loc = ev.get("location")
        parts = []
        if participants:
            parts.append(f"[{', '.join(participants[:3])}]")
        if loc:
            parts.append(f"@ {loc}")
        context = f" ({' '.join(parts)})" if parts else ""
        lines.append(f"- {marker}{summary}{marker}{context}")
    lines.append("")


def _render_timeline_outline(lines: list[str], events: list[dict]) -> None:
    """Author template: condensed timeline outline (high importance only)."""
    # Only show high importance events for outline
    high_events = [e for e in events if e.get("importance") == "high"]
    if not high_events:
        high_events = events[:30]  # Fallback: first 30 events

    current_chapter = -1
    for ev in high_events[:50]:
        ch_num = ev.get("chapter", 0)
        if ch_num != current_chapter:
            current_chapter = ch_num
            lines.append(f"\n**第{ch_num}章**\n")
        summary = ev.get("summary", "")
        participants = ev.get("participants", [])
        p_str = f" [{', '.join(participants[:2])}]" if participants else ""
        lines.append(f"- {summary}{p_str}")
    lines.append("")


def _escape_pipe(text: str) -> str:
    """Escape pipe characters for Markdown table cells."""
    return text.replace("|", "\\|")


def _cat_label(category: str) -> str:
    """Translate relation category to Chinese label."""
    return {
        "family": "亲属",
        "intimate": "亲密",
        "social": "社交",
        "hostile": "敌对",
        "hierarchical": "上下级",
        "other": "其他",
    }.get(category, category)

"""Series Bible PDF renderer — convert collected data to styled PDF.

Uses reportlab Platypus for document layout with Chinese font support.
Falls back to Helvetica if no CJK font is registered.
"""

from __future__ import annotations

import io
import logging

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.services.series_bible_service import SeriesBibleData

logger = logging.getLogger(__name__)

# ── CJK font registration ─────────────────────────

_CJK_FONT_REGISTERED = False
_CJK_FONT_NAME = "Helvetica"  # fallback


def _ensure_cjk_font() -> str:
    """Register a CJK font if available. Returns font name to use."""
    global _CJK_FONT_REGISTERED, _CJK_FONT_NAME
    if _CJK_FONT_REGISTERED:
        return _CJK_FONT_NAME

    _CJK_FONT_REGISTERED = True

    # Try reportlab's built-in CIDFont for Chinese
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont

        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        _CJK_FONT_NAME = "STSong-Light"
        return _CJK_FONT_NAME
    except Exception:
        pass

    # Try system fonts (macOS)
    try:
        import os

        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        font_paths = [
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/Supplemental/Songti.ttc",
            # Windows
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simsun.ttc",
            # Linux
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ]
        for fp in font_paths:
            if os.path.exists(fp):
                name = os.path.splitext(os.path.basename(fp))[0].replace(" ", "")
                pdfmetrics.registerFont(TTFont(name, fp, subfontIndex=0))
                _CJK_FONT_NAME = name
                return _CJK_FONT_NAME
    except Exception as e:
        logger.debug("CJK font registration failed: %s", e)

    logger.warning("No CJK font found, PDF may not display Chinese correctly")
    return _CJK_FONT_NAME


# ── Styles ─────────────────────────────────────────


def _build_styles() -> dict[str, ParagraphStyle]:
    """Build paragraph styles with CJK font."""
    font = _ensure_cjk_font()
    base = getSampleStyleSheet()

    return {
        "title": ParagraphStyle(
            "PDFTitle",
            parent=base["Title"],
            fontName=font,
            fontSize=22,
            alignment=TA_CENTER,
            spaceAfter=12,
        ),
        "subtitle": ParagraphStyle(
            "PDFSubtitle",
            parent=base["Normal"],
            fontName=font,
            fontSize=11,
            alignment=TA_CENTER,
            textColor=colors.gray,
            spaceAfter=24,
        ),
        "h1": ParagraphStyle(
            "PDFH1",
            parent=base["Heading1"],
            fontName=font,
            fontSize=16,
            spaceBefore=18,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "PDFH2",
            parent=base["Heading2"],
            fontName=font,
            fontSize=13,
            spaceBefore=12,
            spaceAfter=6,
        ),
        "h3": ParagraphStyle(
            "PDFH3",
            parent=base["Heading3"],
            fontName=font,
            fontSize=11,
            spaceBefore=8,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "PDFBody",
            parent=base["Normal"],
            fontName=font,
            fontSize=10,
            leading=14,
            spaceAfter=4,
        ),
        "bullet": ParagraphStyle(
            "PDFBullet",
            parent=base["Normal"],
            fontName=font,
            fontSize=10,
            leading=14,
            leftIndent=20,
            spaceAfter=2,
        ),
        "label": ParagraphStyle(
            "PDFLabel",
            parent=base["Normal"],
            fontName=font,
            fontSize=10,
            leading=14,
            spaceAfter=2,
        ),
        "footer": ParagraphStyle(
            "PDFFooter",
            parent=base["Normal"],
            fontName=font,
            fontSize=8,
            alignment=TA_CENTER,
            textColor=colors.gray,
        ),
    }


# ── Main render function ──────────────────────────


def render_pdf(data: SeriesBibleData) -> io.BytesIO:
    """Render SeriesBibleData as a PDF document. Returns BytesIO buffer."""
    buf = io.BytesIO()
    styles = _build_styles()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        title=f"{data.novel_title} - 设定集",
        author="AI Reader V2",
    )

    story: list = []

    # ── Title page ────────────────────────────────
    story.append(Spacer(1, 3 * cm))
    story.append(Paragraph(_esc(data.novel_title), styles["title"]))
    if data.novel_author:
        story.append(Paragraph(f"作者: {_esc(data.novel_author)}", styles["subtitle"]))
    story.append(
        Paragraph(
            f"分析范围: 第 {data.chapter_range[0]} ~ {data.chapter_range[1]} 章",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, 2 * cm))

    modules = data.modules or [
        "characters", "relations", "locations", "items", "orgs", "timeline",
    ]

    # ── Characters ────────────────────────────────
    if "characters" in modules and data.characters:
        story.append(Paragraph("人物档案", styles["h1"]))
        _render_characters(story, styles, data.characters)

    # ── Relations ─────────────────────────────────
    if "relations" in modules and data.relations:
        story.append(Paragraph("关系网络", styles["h1"]))
        _render_relations(story, styles, data.relations)

    # ── Locations ─────────────────────────────────
    if "locations" in modules and data.locations:
        story.append(Paragraph("地点百科", styles["h1"]))
        _render_locations(story, styles, data.locations)

    # ── Items ─────────────────────────────────────
    if "items" in modules and data.items:
        story.append(Paragraph("物品道具", styles["h1"]))
        _render_items(story, styles, data.items)

    # ── Orgs ──────────────────────────────────────
    if "orgs" in modules and data.orgs:
        story.append(Paragraph("组织势力", styles["h1"]))
        _render_orgs(story, styles, data.orgs)

    # ── Timeline ──────────────────────────────────
    if "timeline" in modules and data.timeline:
        story.append(Paragraph("时间线", styles["h1"]))
        _render_timeline(story, styles, data.timeline)

    # ── Footer ────────────────────────────────────
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph("由 AI Reader V2 自动生成", styles["footer"]))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    buf.seek(0)
    return buf


# ── Section renderers ─────────────────────────────


def _render_characters(story: list, styles: dict, characters: list[dict]) -> None:
    for ch in characters:
        story.append(Paragraph(_esc(ch["name"]), styles["h2"]))

        aliases = ch.get("aliases", [])
        alias_names = [a["name"] for a in aliases if a["name"] != ch["name"]]
        if alias_names:
            story.append(
                Paragraph(f"<b>别称:</b> {_esc(', '.join(alias_names))}", styles["body"])
            )

        appearances = ch.get("appearances", [])
        if appearances:
            story.append(Paragraph("<b>外貌特征:</b>", styles["label"]))
            for ap in appearances[:3]:
                story.append(
                    Paragraph(f"• {_esc(ap['description'])}", styles["bullet"])
                )

        abilities = ch.get("abilities", [])
        if abilities:
            story.append(Paragraph("<b>能力:</b>", styles["label"]))
            for ab in abilities[:5]:
                dim = ab.get("dimension", "")
                name = ab.get("name", "")
                desc = ab.get("description", "")
                story.append(
                    Paragraph(
                        f"• <b>{_esc(dim)}·{_esc(name)}</b>: {_esc(desc)}",
                        styles["bullet"],
                    )
                )

        relations = ch.get("relations", [])
        if relations:
            story.append(Paragraph("<b>人物关系:</b>", styles["label"]))
            for rel in relations[:10]:
                other = rel.get("other_person", "")
                category = rel.get("category", "other")
                stages = rel.get("stages", [])
                if len(stages) > 1:
                    chain = " → ".join(s.get("relation_type", "") for s in stages)
                    story.append(
                        Paragraph(
                            f"• {_esc(other)} — {_esc(chain)} ({_cat_label(category)})",
                            styles["bullet"],
                        )
                    )
                elif stages:
                    rel_type = stages[0].get("relation_type", "")
                    story.append(
                        Paragraph(
                            f"• {_esc(other)} — {_esc(rel_type)} ({_cat_label(category)})",
                            styles["bullet"],
                        )
                    )

        experiences = ch.get("experiences", [])
        if experiences:
            story.append(Paragraph("<b>主要经历:</b>", styles["label"]))
            for exp in experiences[:8]:
                ch_num = exp.get("chapter", "")
                summary = exp.get("summary", "")
                loc = exp.get("location")
                loc_str = f" @ {loc}" if loc else ""
                story.append(
                    Paragraph(
                        f"• [第{ch_num}章] {_esc(summary)}{_esc(loc_str)}",
                        styles["bullet"],
                    )
                )

        stats = ch.get("stats", {})
        if stats:
            story.append(
                Paragraph(
                    f"<i>出场章数: {stats.get('chapter_count', 0)} · "
                    f"首次出场: 第{stats.get('first_chapter', '?')}章</i>",
                    styles["body"],
                )
            )
        story.append(Spacer(1, 6))


def _render_relations(story: list, styles: dict, relations: dict) -> None:
    edges = relations.get("edges", [])
    if not edges:
        story.append(Paragraph("暂无关系数据", styles["body"]))
        return

    sorted_edges = sorted(edges, key=lambda e: e.get("weight", 0), reverse=True)
    display = sorted_edges[:30]

    font = _ensure_cjk_font()
    header_style = ParagraphStyle("TH", fontName=font, fontSize=9, alignment=TA_CENTER)
    cell_style = ParagraphStyle("TC", fontName=font, fontSize=9, alignment=TA_LEFT)

    table_data = [
        [
            Paragraph("<b>人物A</b>", header_style),
            Paragraph("<b>人物B</b>", header_style),
            Paragraph("<b>关系</b>", header_style),
            Paragraph("<b>章节数</b>", header_style),
        ]
    ]
    for edge in display:
        table_data.append([
            Paragraph(_esc(edge.get("source", "")), cell_style),
            Paragraph(_esc(edge.get("target", "")), cell_style),
            Paragraph(_esc(edge.get("relation_type", "")), cell_style),
            Paragraph(str(edge.get("weight", 0)), cell_style),
        ])

    col_widths = [4.5 * cm, 4.5 * cm, 4 * cm, 2 * cm]
    table = Table(table_data, colWidths=col_widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
    ]))
    story.append(table)
    story.append(Spacer(1, 8))


def _render_locations(story: list, styles: dict, locations: list[dict]) -> None:
    for loc in locations:
        story.append(Paragraph(_esc(loc["name"]), styles["h2"]))

        meta = []
        loc_type = loc.get("location_type", "")
        parent = loc.get("parent")
        if loc_type:
            meta.append(f"类型: {loc_type}")
        if parent:
            meta.append(f"上级: {parent}")
        if meta:
            story.append(Paragraph(f"<b>{_esc(' · '.join(meta))}</b>", styles["body"]))

        children = loc.get("children", [])
        if children:
            story.append(
                Paragraph(
                    f"<b>下级地点:</b> {_esc(', '.join(children[:10]))}",
                    styles["body"],
                )
            )

        descriptions = loc.get("descriptions", [])
        if descriptions:
            for desc in descriptions[:3]:
                story.append(
                    Paragraph(f"• {_esc(desc.get('description', ''))}", styles["bullet"])
                )

        visitors = loc.get("visitors", [])
        if visitors:
            visitor_names = [
                f"{v['name']}{'(常驻)' if v.get('is_resident') else ''}"
                for v in visitors[:8]
            ]
            story.append(
                Paragraph(
                    f"<b>到访者:</b> {_esc(', '.join(visitor_names))}",
                    styles["body"],
                )
            )

        stats = loc.get("stats", {})
        if stats:
            story.append(
                Paragraph(
                    f"<i>提及章数: {stats.get('chapter_count', 0)} · "
                    f"首次出现: 第{stats.get('first_chapter', '?')}章</i>",
                    styles["body"],
                )
            )


def _render_items(story: list, styles: dict, items: list[dict]) -> None:
    for item in items:
        story.append(Paragraph(_esc(item["name"]), styles["h2"]))

        item_type = item.get("item_type", "")
        if item_type:
            story.append(Paragraph(f"<b>类型:</b> {_esc(item_type)}", styles["body"]))

        flow = item.get("flow", [])
        if flow:
            story.append(Paragraph("<b>流转记录:</b>", styles["label"]))
            for f in flow[:8]:
                ch_num = f.get("chapter", "")
                action = f.get("action", "")
                actor = f.get("actor", "")
                desc = f.get("description", "")
                text = f"[第{ch_num}章] {actor} {action}"
                if desc:
                    text += f" — {desc}"
                story.append(Paragraph(f"• {_esc(text)}", styles["bullet"]))


def _render_orgs(story: list, styles: dict, orgs: list[dict]) -> None:
    for org in orgs:
        story.append(Paragraph(_esc(org["name"]), styles["h2"]))

        org_type = org.get("org_type", "")
        if org_type:
            story.append(Paragraph(f"<b>类型:</b> {_esc(org_type)}", styles["body"]))

        members = org.get("member_events", [])
        if members:
            story.append(Paragraph("<b>成员变动:</b>", styles["label"]))
            for m in members[:10]:
                ch_num = m.get("chapter", "")
                member = m.get("member", "")
                action = m.get("action", "")
                role = m.get("role", "")
                role_str = f" ({role})" if role else ""
                story.append(
                    Paragraph(
                        f"• [第{ch_num}章] {_esc(member)}{_esc(role_str)} — {_esc(action)}",
                        styles["bullet"],
                    )
                )

        org_rels = org.get("org_relations", [])
        if org_rels:
            story.append(Paragraph("<b>组织关系:</b>", styles["label"]))
            for r in org_rels[:5]:
                story.append(
                    Paragraph(
                        f"• {_esc(r.get('other_org', ''))} — {_esc(r.get('relation_type', ''))}",
                        styles["bullet"],
                    )
                )


def _render_timeline(story: list, styles: dict, events: list[dict]) -> None:
    current_chapter = -1
    for ev in events[:100]:
        ch_num = ev.get("chapter", 0)
        if ch_num != current_chapter:
            current_chapter = ch_num
            story.append(Paragraph(f"第{ch_num}章", styles["h3"]))

        importance = ev.get("importance", "medium")
        summary = ev.get("summary", "")
        participants = ev.get("participants", [])
        loc = ev.get("location")

        parts = []
        if participants:
            parts.append(f"[{', '.join(participants[:3])}]")
        if loc:
            parts.append(f"@ {loc}")
        context = f" ({' '.join(parts)})" if parts else ""

        text = f"{_esc(summary)}{_esc(context)}"
        if importance == "high":
            text = f"<b>{text}</b>"

        story.append(Paragraph(f"• {text}", styles["bullet"]))


# ── Helpers ───────────────────────────────────────


def _header_footer(canvas, doc):
    """Draw page header and footer on each page."""
    canvas.saveState()
    font = _ensure_cjk_font()

    # Header
    canvas.setFont(font, 8)
    canvas.setFillColor(colors.gray)
    canvas.drawCentredString(
        A4[0] / 2, A4[1] - 1.2 * cm, doc.title or ""
    )

    # Footer — page number
    canvas.drawCentredString(
        A4[0] / 2, 1 * cm, f"- {canvas.getPageNumber()} -"
    )
    canvas.restoreState()


def _esc(text: str) -> str:
    """Escape XML special characters for Paragraph markup."""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


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

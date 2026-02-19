"""Series Bible export endpoint."""

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

router = APIRouter(prefix="/api/novels", tags=["series-bible"])


class SeriesBibleRequest(BaseModel):
    modules: list[str] | None = None
    template: str = "complete"  # "complete" | "author"
    format: str = "markdown"  # "markdown" | "docx" | "xlsx" | "pdf"
    chapter_start: int | None = None
    chapter_end: int | None = None


@router.post("/{novel_id}/series-bible/export")
async def export_series_bible(novel_id: str, req: SeriesBibleRequest | None = None):
    """Export Series Bible as Markdown, Word, Excel, or PDF file."""
    from src.services.series_bible_renderer import TEMPLATES, render_markdown
    from src.services.series_bible_service import collect_data

    body = req or SeriesBibleRequest()
    template = body.template if body.template in TEMPLATES else "complete"

    try:
        data = await collect_data(
            novel_id=novel_id,
            modules=body.modules,
            chapter_start=body.chapter_start,
            chapter_end=body.chapter_end,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if body.format == "docx":
        from src.services.docx_renderer import render_docx

        buf = render_docx(data)
        filename = f"{data.novel_title}_设定集.docx"
        return Response(
            content=buf.read(),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            },
        )

    if body.format == "xlsx":
        from src.services.xlsx_renderer import render_xlsx

        buf = render_xlsx(data)
        filename = f"{data.novel_title}_设定集.xlsx"
        return Response(
            content=buf.read(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            },
        )

    if body.format == "pdf":
        from src.services.pdf_renderer import render_pdf

        buf = render_pdf(data)
        filename = f"{data.novel_title}_设定集.pdf"
        return Response(
            content=buf.read(),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            },
        )

    md_content = render_markdown(data, template=template)
    tpl_name = TEMPLATES[template]["name"]
    filename = f"{data.novel_title}_{tpl_name}.md"

    return Response(
        content=md_content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@router.get("/{novel_id}/series-bible/templates")
async def get_templates(novel_id: str):
    """Return available export templates."""
    from src.services.series_bible_renderer import get_template_info

    return {"templates": get_template_info()}

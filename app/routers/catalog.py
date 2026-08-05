from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import get_db
from ..models import ChapterOut, PageSummary

router = APIRouter(prefix="/api", tags=["catalog"])


@router.get("/chapters", response_model=list[ChapterOut])
def list_chapters() -> list[ChapterOut]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.slug, c.title, COUNT(p.id) AS page_count
            FROM chapter c
            LEFT JOIN page p ON p.chapter_id = c.id
            GROUP BY c.id
            ORDER BY c.title
            """
        ).fetchall()
    return [
        ChapterOut(
            id=r["id"],
            slug=r["slug"],
            title=r["title"],
            page_count=int(r["page_count"]),
        )
        for r in rows
    ]


@router.get("/chapters/{slug}/pages", response_model=list[PageSummary])
def list_pages(slug: str) -> list[PageSummary]:
    with get_db() as conn:
        chapter = conn.execute(
            "SELECT id, slug FROM chapter WHERE slug = ?", (slug,)
        ).fetchone()
        if chapter is None:
            raise HTTPException(404, f"chapter not found: {slug}")
        rows = conn.execute(
            """
            SELECT p.id, p.page_no, p.stem, p.width, p.height,
                   p.clean_status, p.typeset_status, p.bubble_count,
                   COALESCE(e.done_count, 0) AS done_count,
                   COALESCE(e.translated_count, 0) AS translated_count
            FROM page p
            LEFT JOIN page_edit e ON e.page_id = p.id
            WHERE p.chapter_id = ?
            ORDER BY p.page_no
            """,
            (chapter["id"],),
        ).fetchall()
    return [
        PageSummary(
            id=r["id"],
            page_no=r["page_no"],
            stem=r["stem"],
            width=r["width"],
            height=r["height"],
            thumb_url=f"/api/assets/{slug}/{r['stem']}/{r['stem']}.png",
            clean_status=r["clean_status"],
            typeset_status=r["typeset_status"],
            bubble_count=r["bubble_count"],
            done_count=int(r["done_count"]),
            translated_count=int(r["translated_count"]),
        )
        for r in rows
    ]

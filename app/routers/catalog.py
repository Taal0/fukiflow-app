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
            SELECT c.id, c.slug, c.title,
                   COALESCE(c.source_type, 'seed') AS source_type,
                   c.created_at,
                   COUNT(p.id) AS page_count,
                   (
                     SELECT p2.stem FROM page p2
                     WHERE p2.chapter_id = c.id
                     ORDER BY p2.page_no
                     LIMIT 1
                   ) AS cover_stem,
                   (
                     SELECT COALESCE(SUM(e.done_count), 0)
                     FROM page p3
                     LEFT JOIN page_edit e ON e.page_id = p3.id
                     WHERE p3.chapter_id = c.id
                   ) AS done_count,
                   (
                     SELECT COALESCE(SUM(p4.bubble_count), 0)
                     FROM page p4
                     WHERE p4.chapter_id = c.id
                   ) AS bubble_count,
                   (
                     SELECT COUNT(*) FROM page p5
                     WHERE p5.chapter_id = c.id
                       AND COALESCE(p5.processing_status, 'ready') IN ('pending', 'processing')
                   ) AS processing_count,
                   (
                     SELECT COUNT(*) FROM page p6
                     WHERE p6.chapter_id = c.id
                       AND COALESCE(p6.processing_status, 'ready') = 'error'
                   ) AS error_count
            FROM chapter c
            LEFT JOIN page p ON p.chapter_id = c.id
            GROUP BY c.id
            ORDER BY c.created_at DESC, c.title
            """
        ).fetchall()
    out: list[ChapterOut] = []
    for r in rows:
        stem = r["cover_stem"]
        cover = (
            f"/api/assets/{r['slug']}/{stem}/{stem}.png" if stem else None
        )
        out.append(
            ChapterOut(
                id=r["id"],
                slug=r["slug"],
                title=r["title"],
                page_count=int(r["page_count"]),
                source_type=r["source_type"] or "seed",
                created_at=r["created_at"],
                cover_url=cover,
                done_count=int(r["done_count"] or 0),
                bubble_count=int(r["bubble_count"] or 0),
                processing_count=int(r["processing_count"] or 0),
                error_count=int(r["error_count"] or 0),
            )
        )
    return out


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
                   COALESCE(p.processing_status, 'ready') AS processing_status,
                   p.error_message,
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
            processing_status=r["processing_status"] or "ready",
            error_message=r["error_message"],
        )
        for r in rows
    ]

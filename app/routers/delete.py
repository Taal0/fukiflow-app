"""Delete chapters and pages (seed and upload)."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, HTTPException

from ..db import get_db
from ..storage import chapter_dir

router = APIRouter(prefix="/api", tags=["delete"])


@router.delete("/chapters/{slug}")
def delete_chapter(slug: str):
    """Delete a chapter (DB + disk). Seed and upload both allowed."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, slug, source_type FROM chapter WHERE slug = ?",
            (slug,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"chapter not found: {slug}")
        source_type = row["source_type"] or "seed"

        chapter_id = int(row["id"])
        # Cascade deletes page / page_asset / layer_asset / page_edit via FK.
        conn.execute("DELETE FROM chapter WHERE id = ?", (chapter_id,))

    path = chapter_dir(slug, source_type)
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)

    return {"ok": True, "slug": slug}


@router.delete("/pages/{page_id}")
def delete_page(page_id: int):
    """Delete a single page (seed and upload)."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT p.id, p.stem, c.id AS chapter_id, c.slug, c.source_type
            FROM page p
            JOIN chapter c ON c.id = p.chapter_id
            WHERE p.id = ?
            """,
            (page_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"page not found: {page_id}")

        source_type = row["source_type"] or "seed"

        slug = row["slug"]
        stem = row["stem"]
        chapter_id = int(row["chapter_id"])

        conn.execute("DELETE FROM page WHERE id = ?", (page_id,))

        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM page WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        empty = int(remaining["n"]) == 0
        if empty:
            conn.execute("DELETE FROM chapter WHERE id = ?", (chapter_id,))

    page_path = chapter_dir(slug, source_type) / stem
    if page_path.is_dir():
        shutil.rmtree(page_path, ignore_errors=True)

    if empty:
        chap_path = chapter_dir(slug, source_type)
        if chap_path.is_dir():
            shutil.rmtree(chap_path, ignore_errors=True)
        return {"ok": True, "page_id": page_id, "chapter_deleted": True, "slug": slug}

    return {"ok": True, "page_id": page_id, "chapter_deleted": False, "slug": slug}

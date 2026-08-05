"""PageEdit overlay CRUD + chapter progress.

``bubbles[].text`` is the letterer / translator write surface — future
translator UI will PUT the same overlay endpoint.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import get_db
from ..edits import (
    ConflictError,
    delete_page_edit,
    empty_edit,
    load_page_edit,
    upsert_page_edit,
    validate_edit,
)
from ..models import ChapterProgress, PageEditOut, PageEditPut, PageProgress

router = APIRouter(prefix="/api", tags=["edits"])


def _ensure_page(conn, page_id: int) -> None:
    row = conn.execute("SELECT id FROM page WHERE id = ?", (page_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"page not found: {page_id}")


@router.get("/pages/{page_id}/edit", response_model=PageEditOut)
def get_page_edit(page_id: int) -> PageEditOut:
    with get_db() as conn:
        _ensure_page(conn, page_id)
        loaded = load_page_edit(conn, page_id)
    if loaded is None:
        return PageEditOut(rev=0, edit=empty_edit(rev=0), author=None)
    edit, rev, author = loaded
    return PageEditOut(rev=rev, edit=edit, author=author)


@router.put("/pages/{page_id}/edit", response_model=PageEditOut)
def put_page_edit(page_id: int, body: PageEditPut) -> PageEditOut:
    """Persist overlay. Optional ``user_id`` is stored as ``page_edit.author``
    (roller seam; ignored by single-user letterer clients).
    """
    try:
        validated = validate_edit(body.edit)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    # Client sends expected current rev in body.rev (and optionally edit.rev).
    expected = body.rev
    validated["rev"] = expected

    with get_db() as conn:
        _ensure_page(conn, page_id)
        try:
            saved, author = upsert_page_edit(
                conn,
                page_id,
                validated,
                expected_rev=expected,
                author=body.user_id,
            )
        except ConflictError as e:
            raise HTTPException(
                409,
                {
                    "message": "edit conflict",
                    "current_rev": e.current_rev,
                },
            ) from e
    return PageEditOut(rev=saved["rev"], edit=saved, author=author)


@router.delete("/pages/{page_id}/edit", response_model=dict)
def reset_page_edit(page_id: int) -> dict:
    with get_db() as conn:
        _ensure_page(conn, page_id)
        deleted = delete_page_edit(conn, page_id)
    return {"ok": True, "deleted": deleted}


@router.get("/chapters/{slug}/progress", response_model=ChapterProgress)
def chapter_progress(slug: str) -> ChapterProgress:
    with get_db() as conn:
        chapter = conn.execute(
            "SELECT id, slug FROM chapter WHERE slug = ?", (slug,)
        ).fetchone()
        if chapter is None:
            raise HTTPException(404, f"chapter not found: {slug}")
        rows = conn.execute(
            """
            SELECT p.id, p.page_no, p.bubble_count,
                   COALESCE(e.done_count, 0) AS done_count
            FROM page p
            LEFT JOIN page_edit e ON e.page_id = p.id
            WHERE p.chapter_id = ?
            ORDER BY p.page_no
            """,
            (chapter["id"],),
        ).fetchall()
    return ChapterProgress(
        slug=slug,
        pages=[
            PageProgress(
                page_id=r["id"],
                page_no=r["page_no"],
                bubble_count=r["bubble_count"],
                done_count=int(r["done_count"]),
            )
            for r in rows
        ],
    )

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from ..db import get_db
from ..models import ComposeRequest
from ..settings import get_settings
from ..vision_client import vision_compose

router = APIRouter(prefix="/api", tags=["compose"])


@router.post("/pages/{page_id}/compose")
async def compose_page(
    page_id: int,
    body: ComposeRequest,
    download: int = Query(0, description="1 → Content-Disposition attachment"),
):
    settings = get_settings()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT p.*, c.slug AS chapter_slug
            FROM page p
            JOIN chapter c ON c.id = p.chapter_id
            WHERE p.id = ?
            """,
            (page_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"page not found: {page_id}")
        original = conn.execute(
            "SELECT rel_path FROM page_asset WHERE page_id = ? AND kind = 'original'",
            (page_id,),
        ).fetchone()

    if original is None:
        raise HTTPException(500, "original asset missing for page")
    orig_path = settings.seed_dir / original["rel_path"]
    if not orig_path.is_file():
        raise HTTPException(500, f"original image missing: {orig_path}")

    if body.only_kind and body.only_kind not in {"clean", "text"}:
        raise HTTPException(400, "only_kind must be 'clean' or 'text'")

    image_bytes = orig_path.read_bytes()
    doc_json = json.dumps(body.doc)
    off = ",".join(body.off) if body.off else None

    png = await vision_compose(
        image_bytes=image_bytes,
        filename=orig_path.name,
        doc_json=doc_json,
        off=off,
        only_kind=body.only_kind,
        font=body.font,
    )

    headers = {}
    if download:
        stem = row["stem"]
        headers["Content-Disposition"] = f'attachment; filename="{stem}-compose.png"'
    return Response(content=png, media_type="image/png", headers=headers)

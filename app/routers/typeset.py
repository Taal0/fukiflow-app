from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from vision.document import PageDoc

from ..db import get_db
from ..models import TypesetRequest
from ..settings import get_settings
from ..vision_client import vision_typeset

router = APIRouter(prefix="/api", tags=["typeset"])


@router.post("/pages/{page_id}/typeset")
async def typeset_page(page_id: int, body: TypesetRequest):
    """Proxy to vision typeset.

    ``body.overrides`` may include per-bubble ``font_px``, ``font_scale``,
    ``font``, ``frame`` ``[x0,y0,x1,y1]``, ``leading``, and ``align``.
    """
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
        cleaned = conn.execute(
            "SELECT rel_path FROM page_asset WHERE page_id = ? AND kind = 'cleaned'",
            (page_id,),
        ).fetchone()

    slug = row["chapter_slug"]
    stem = row["stem"]
    doc_path = settings.seed_dir / slug / stem / f"{stem}-doc.json"
    if not doc_path.is_file():
        raise HTTPException(500, f"PageDoc missing: {doc_path}")

    try:
        doc = PageDoc.model_validate(json.loads(doc_path.read_text(encoding="utf-8")))
    except Exception as e:
        raise HTTPException(500, f"invalid PageDoc: {e}") from e

    if cleaned is None:
        raise HTTPException(500, "cleaned asset missing for page")
    cleaned_path = settings.seed_dir / cleaned["rel_path"]
    if not cleaned_path.is_file():
        raise HTTPException(500, f"cleaned image missing: {cleaned_path}")

    image_bytes = cleaned_path.read_bytes()
    bubbles_json = json.dumps([b.model_dump() for b in doc.bubbles])
    texts_json = json.dumps([t.model_dump() for t in body.texts])
    overrides_json = (
        json.dumps(body.overrides) if body.overrides is not None else None
    )

    result = await vision_typeset(
        image_bytes=image_bytes,
        filename=cleaned_path.name,
        bubbles_json=bubbles_json,
        texts_json=texts_json,
        font=body.font,
        overrides_json=overrides_json,
        include_layers=True,
    )
    return {
        "layouts": result.get("layouts", []),
        "text_layers": result.get("text_layers", []),
        "typeset": result.get("typeset"),
        "typeset_regions": result.get("typeset_regions", []),
    }

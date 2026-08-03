from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..db import get_db
from ..models import AssetUrls, LayerAssetOut, PageDetail, PageMeta
from ..settings import get_settings

router = APIRouter(prefix="/api", tags=["pages"])


def _asset_url(chapter_slug: str, stem: str, rel_path: str) -> str:
    # rel_path is relative to seed_dir, e.g. test-manga/page_1/page_1.png
    name = Path(rel_path).name
    # Layer crops live under <stem>-layers-crop/
    parent = Path(rel_path).parent.name
    if parent.endswith("-layers-crop") or parent.endswith("-layers"):
        return f"/api/assets/{chapter_slug}/{stem}/{parent}/{name}"
    return f"/api/assets/{chapter_slug}/{stem}/{name}"


@router.get("/pages/{page_id}", response_model=PageDetail)
def get_page(page_id: int) -> PageDetail:
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

        assets_rows = conn.execute(
            "SELECT kind, rel_path FROM page_asset WHERE page_id = ?",
            (page_id,),
        ).fetchall()
        layer_rows = conn.execute(
            """
            SELECT layer_id, kind, bubble_id, rel_path,
                   offset_x, offset_y, width, height
            FROM layer_asset
            WHERE page_id = ?
            ORDER BY layer_id
            """,
            (page_id,),
        ).fetchall()

    slug = row["chapter_slug"]
    stem = row["stem"]
    doc_path = settings.seed_dir / slug / stem / f"{stem}-doc.json"
    if not doc_path.is_file():
        raise HTTPException(500, f"PageDoc missing on disk: {doc_path}")
    try:
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"invalid PageDoc: {e}") from e

    urls = AssetUrls()
    for a in assets_rows:
        url = _asset_url(slug, stem, a["rel_path"])
        if a["kind"] == "original":
            urls.original_url = url
        elif a["kind"] == "cleaned":
            urls.cleaned_url = url
        elif a["kind"] == "compose":
            urls.compose_url = url
        elif a["kind"] == "detect":
            urls.detect_url = url

    layers = [
        LayerAssetOut(
            layer_id=L["layer_id"],
            kind=L["kind"],
            bubble_id=L["bubble_id"],
            url=_asset_url(slug, stem, L["rel_path"]),
            offset_x=L["offset_x"],
            offset_y=L["offset_y"],
            width=L["width"],
            height=L["height"],
        )
        for L in layer_rows
    ]

    return PageDetail(
        page=PageMeta(
            id=row["id"],
            chapter_id=row["chapter_id"],
            chapter_slug=slug,
            page_no=row["page_no"],
            stem=stem,
            width=row["width"],
            height=row["height"],
            dpi=row["dpi"],
            clean_status=row["clean_status"],
            typeset_status=row["typeset_status"],
            bubble_count=row["bubble_count"],
            editor_bubble_count=row["editor_bubble_count"],
        ),
        doc=doc,
        assets=urls,
        layer_assets=layers,
    )

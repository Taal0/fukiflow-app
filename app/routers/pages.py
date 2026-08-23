from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..db import get_db
from ..edits import empty_edit, load_page_edit
from ..models import AssetUrls, LayerAssetOut, PageDetail, PageMeta
from ..storage import chapter_dir
from ..vision_client import vision_pipeline
from .upload import _persist_pipeline_page

router = APIRouter(prefix="/api", tags=["pages"])


def _asset_url(chapter_slug: str, stem: str, rel_path: str) -> str:
    # rel_path is relative to data root, e.g. test-manga/page_1/page_1.png
    name = Path(rel_path).name
    # Layer crops live under <stem>-layers-crop/
    parent = Path(rel_path).parent.name
    if parent.endswith("-layers-crop") or parent.endswith("-layers"):
        return f"/api/assets/{chapter_slug}/{stem}/{parent}/{name}"
    return f"/api/assets/{chapter_slug}/{stem}/{name}"


@router.get("/pages/{page_id}", response_model=PageDetail)
def get_page(page_id: int) -> PageDetail:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT p.*, c.slug AS chapter_slug,
                   COALESCE(c.source_type, 'seed') AS source_type
            FROM page p
            JOIN chapter c ON c.id = p.chapter_id
            WHERE p.id = ?
            """,
            (page_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"page not found: {page_id}")

        status = row["processing_status"] or "ready"
        if status != "ready":
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        row["error_message"]
                        if status == "error" and row["error_message"]
                        else "Sayfa henüz hazır değil"
                    ),
                    "processing_status": status,
                    "error_message": row["error_message"],
                },
            )

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
        loaded_edit = load_page_edit(conn, page_id)

    slug = row["chapter_slug"]
    stem = row["stem"]
    source_type = row["source_type"] or "seed"
    doc_path = chapter_dir(slug, source_type) / stem / f"{stem}-doc.json"
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

    if loaded_edit is None:
        edit_doc = empty_edit(rev=0)
        edit_rev = 0
        edit_author = None
    else:
        edit_doc, edit_rev, edit_author = loaded_edit

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
        edit=edit_doc,
        edit_rev=edit_rev,
        edit_author=edit_author,
    )


@router.post("/pages/{page_id}/reprocess")
async def reprocess_page(page_id: int):
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT p.id, p.stem, c.slug,
                   COALESCE(c.source_type, 'seed') AS source_type
            FROM page p JOIN chapter c ON c.id = p.chapter_id
            WHERE p.id = ?
            """,
            (page_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"page not found: {page_id}")
        slug = row["slug"]
        stem = row["stem"]
        source_type = row["source_type"] or "seed"

    if source_type != "upload":
        raise HTTPException(400, "sadece upload sayfaları yeniden işlenebilir")

    page_dir = chapter_dir(slug, source_type) / stem
    orig_path = page_dir / f"{stem}-orig.png"
    if not orig_path.is_file():
        raise HTTPException(
            422,
            "orijinal görsel bulunamadı — sayfayı silip yeniden yükleyin",
        )
    png = orig_path.read_bytes()

    with get_db() as conn:
        conn.execute(
            "UPDATE page SET processing_status='processing', error_message=NULL WHERE id=?",
            (page_id,),
        )

    try:
        payload = await vision_pipeline(
            image_bytes=png,
            filename=f"{stem}.png",
            page_id=stem,
            include_layers=True,
        )
        with get_db() as conn:
            _persist_pipeline_page(
                conn,
                page_id=page_id,
                slug=slug,
                stem=stem,
                page_dir=page_dir,
                orig_png=png,
                payload=payload,
            )
    except Exception as e:
        msg = str(getattr(e, "detail", None) or e)
        with get_db() as conn:
            conn.execute(
                "UPDATE page SET processing_status='error', error_message=? WHERE id=?",
                (msg[:2000], page_id),
            )
        # 503 vision kapalı; diğer beklenmeyenler 500 — mesaj korunur.
        status_code = getattr(e, "status_code", 500) or 500
        raise HTTPException(status_code, msg) from e

    return {"ok": True, "page_id": page_id, "processing_status": "ready"}

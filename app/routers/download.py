from __future__ import annotations

import io
import json
import zipfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..db import get_db
from ..settings import get_settings
from ..vision_client import vision_compose

router = APIRouter(prefix="/api", tags=["download"])


class ChapterPageComposeIn(BaseModel):
    page_id: int
    doc: Dict[str, Any]
    off: Optional[List[str]] = None
    font: Optional[str] = None


class ChapterDownloadRequest(BaseModel):
    pages: List[ChapterPageComposeIn] = Field(default_factory=list)


@router.post("/chapters/{slug}/download")
async def download_chapter_zip(slug: str, body: ChapterDownloadRequest):
    """Compose each page via vision and stream a zip (spooled to disk if large)."""
    if not body.pages:
        raise HTTPException(400, "pages required")

    settings = get_settings()
    with get_db() as conn:
        chapter = conn.execute(
            "SELECT id, slug, title FROM chapter WHERE slug = ?", (slug,)
        ).fetchone()
        if chapter is None:
            raise HTTPException(404, f"chapter not found: {slug}")

        page_rows = {
            int(r["id"]): r
            for r in conn.execute(
                """
                SELECT p.id, p.stem, p.page_no
                FROM page p
                WHERE p.chapter_id = ?
                """,
                (chapter["id"],),
            ).fetchall()
        }

        originals = {
            int(r["page_id"]): r["rel_path"]
            for r in conn.execute(
                """
                SELECT a.page_id, a.rel_path
                FROM page_asset a
                JOIN page p ON p.id = a.page_id
                WHERE p.chapter_id = ? AND a.kind = 'original'
                """,
                (chapter["id"],),
            ).fetchall()
        }

    # Compose sequentially; write zip to a spooled buffer (overflow → temp file).
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in body.pages:
            row = page_rows.get(item.page_id)
            if row is None:
                raise HTTPException(400, f"page not in chapter: {item.page_id}")
            rel = originals.get(item.page_id)
            if not rel:
                raise HTTPException(500, f"original missing for page {item.page_id}")
            orig_path = settings.seed_dir / rel
            if not orig_path.is_file():
                raise HTTPException(500, f"original image missing: {orig_path}")

            png = await vision_compose(
                image_bytes=orig_path.read_bytes(),
                filename=orig_path.name,
                doc_json=json.dumps(item.doc),
                off=",".join(item.off) if item.off else None,
                only_kind=None,
                font=item.font,
            )
            name = f"{int(row['page_no']):03d}-{row['stem']}-compose.png"
            zf.writestr(name, png)

    buf.seek(0)
    filename = f"{slug}-compose.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

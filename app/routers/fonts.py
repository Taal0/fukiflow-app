from __future__ import annotations

from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ..settings import get_settings
from ..vision_client import vision_get_fonts

router = APIRouter(prefix="/api", tags=["fonts"])


@router.get("/fonts")
async def list_fonts(refresh: int = 0) -> List[Dict[str, Any]]:
    fonts = await vision_get_fonts(refresh=bool(refresh))
    # Enrich with app-relative URLs for FontFace loading.
    out: List[Dict[str, Any]] = []
    for f in fonts:
        item = dict(f)
        key = item.get("key")
        if key:
            item["url"] = f"/api/fonts/{key}/file"
        out.append(item)
    return out


@router.get("/fonts/{key}/file")
async def font_file(key: str):
    settings = get_settings()
    url = f"{settings.vision_url}/v1/typeset/fonts/{key}/file"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url)
    except httpx.RequestError as e:
        raise HTTPException(503, "vision servisi kapali") from e
    if r.status_code == 404:
        raise HTTPException(404, f"font not found: {key}")
    if r.status_code >= 500:
        raise HTTPException(503, "vision servisi kapali")
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)
    media = r.headers.get("content-type", "application/octet-stream")
    return Response(
        content=r.content,
        media_type=media,
        headers={"Cache-Control": "public, max-age=86400"},
    )

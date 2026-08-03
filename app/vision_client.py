from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import HTTPException

from .settings import get_settings

_fonts_cache: Tuple[float, List[Dict[str, Any]]] | None = None


async def vision_get_fonts(*, refresh: bool = False) -> List[Dict[str, Any]]:
    global _fonts_cache
    settings = get_settings()
    now = time.monotonic()
    if (
        not refresh
        and _fonts_cache is not None
        and (now - _fonts_cache[0]) < settings.fonts_cache_ttl_s
    ):
        return _fonts_cache[1]

    url = f"{settings.vision_url}/v1/typeset/fonts"
    if refresh:
        url += "?refresh=1"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url)
    except httpx.RequestError as e:
        raise HTTPException(503, "vision servisi kapali") from e
    if r.status_code >= 500:
        raise HTTPException(503, "vision servisi kapali")
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)
    data = r.json()
    _fonts_cache = (now, data)
    return data


async def vision_typeset(
    *,
    image_bytes: bytes,
    filename: str,
    bubbles_json: str,
    texts_json: str,
    font: Optional[str] = None,
    overrides_json: Optional[str] = None,
    include_layers: bool = True,
) -> Dict[str, Any]:
    settings = get_settings()
    data: Dict[str, Any] = {
        "bubbles_json": bubbles_json,
        "texts_json": texts_json,
        "include_layers": str(include_layers).lower(),
    }
    if font:
        data["font"] = font
    if overrides_json:
        data["overrides_json"] = overrides_json
    files = {"file": (filename, image_bytes, "image/png")}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{settings.vision_url}/v1/typeset",
                data=data,
                files=files,
            )
    except httpx.RequestError as e:
        raise HTTPException(503, "vision servisi kapali") from e
    if r.status_code >= 500:
        raise HTTPException(503, "vision servisi kapali")
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)
    return r.json()


async def vision_compose(
    *,
    image_bytes: bytes,
    filename: str,
    doc_json: str,
    off: Optional[str] = None,
    only_kind: Optional[str] = None,
    font: Optional[str] = None,
) -> bytes:
    settings = get_settings()
    data: Dict[str, Any] = {"doc_json": doc_json}
    if off:
        data["off"] = off
    if only_kind:
        data["only_kind"] = only_kind
    if font:
        data["font"] = font
    files = {"file": (filename, image_bytes, "image/png")}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{settings.vision_url}/v1/compose",
                data=data,
                files=files,
            )
    except httpx.RequestError as e:
        raise HTTPException(503, "vision servisi kapali") from e
    if r.status_code >= 500:
        raise HTTPException(503, "vision servisi kapali")
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)
    return r.content

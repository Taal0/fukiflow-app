from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

import httpx
from fastapi import HTTPException
from fastapi.responses import Response

from .settings import get_settings

_fonts_cache: Tuple[float, List[Dict[str, Any]]] | None = None

_FULL_UNAVAILABLE = "full vision yapilandirilmadi / full vision not configured"


def _raise_from_response(r: httpx.Response) -> None:
    if r.status_code >= 500:
        raise HTTPException(503, "vision servisi kapali")
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)


def _full_base() -> str:
    settings = get_settings()
    url = (settings.vision_full_url or "").strip()
    if not url:
        raise HTTPException(503, _FULL_UNAVAILABLE)
    return url


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
    _raise_from_response(r)
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
    _raise_from_response(r)
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
    _raise_from_response(r)
    return r.content


async def _post_multipart(
    *,
    base_url: str,
    path: str,
    data: Mapping[str, Any],
    files: Optional[Dict[str, Tuple[str, bytes, str]]] = None,
    timeout: float = 180.0,
) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(
                f"{base_url}{path}",
                data=dict(data),
                files=files,
            )
    except httpx.RequestError as e:
        raise HTTPException(503, "vision servisi kapali") from e


def _file_tuple(
    image_bytes: Optional[bytes],
    filename: Optional[str],
    content_type: str = "image/png",
) -> Optional[Dict[str, Tuple[str, bytes, str]]]:
    if image_bytes is None:
        return None
    name = filename or "page.png"
    return {"file": (name, image_bytes, content_type)}


async def vision_bubbles(
    *,
    image_bytes: Optional[bytes] = None,
    filename: Optional[str] = None,
    image_b64: Optional[str] = None,
    page_id: Optional[str] = None,
) -> Dict[str, Any]:
    base = _full_base()
    data: Dict[str, Any] = {}
    if image_b64:
        data["image_b64"] = image_b64
    if page_id:
        data["page_id"] = page_id
    r = await _post_multipart(
        base_url=base,
        path="/v1/bubbles",
        data=data,
        files=_file_tuple(image_bytes, filename),
    )
    _raise_from_response(r)
    return r.json()


async def vision_order(
    *,
    image_bytes: Optional[bytes] = None,
    filename: Optional[str] = None,
    image_b64: Optional[str] = None,
    page_id: Optional[str] = None,
    include_overlay: bool = True,
) -> Dict[str, Any]:
    base = _full_base()
    data: Dict[str, Any] = {
        "include_overlay": str(include_overlay).lower(),
    }
    if image_b64:
        data["image_b64"] = image_b64
    if page_id:
        data["page_id"] = page_id
    r = await _post_multipart(
        base_url=base,
        path="/v1/order",
        data=data,
        files=_file_tuple(image_bytes, filename),
    )
    _raise_from_response(r)
    return r.json()


async def vision_pipeline(
    *,
    image_bytes: Optional[bytes] = None,
    filename: Optional[str] = None,
    image_b64: Optional[str] = None,
    page_id: Optional[str] = None,
    include_layers: bool = False,
) -> Dict[str, Any]:
    base = _full_base()
    data: Dict[str, Any] = {
        "include_layers": str(include_layers).lower(),
    }
    if image_b64:
        data["image_b64"] = image_b64
    if page_id:
        data["page_id"] = page_id
    r = await _post_multipart(
        base_url=base,
        path="/v1/pipeline",
        data=data,
        files=_file_tuple(image_bytes, filename),
    )
    _raise_from_response(r)
    return r.json()


async def vision_clean(
    *,
    image_bytes: Optional[bytes] = None,
    filename: Optional[str] = None,
    image_b64: Optional[str] = None,
    bubbles_json: str,
    page_id: Optional[str] = None,
    as_base64: bool = False,
    include_layers: bool = False,
) -> Dict[str, Any] | Response:
    """Forward clean to light VISION_URL (no YOLO)."""
    settings = get_settings()
    data: Dict[str, Any] = {
        "bubbles_json": bubbles_json,
        "as_base64": str(as_base64).lower(),
        "include_layers": str(include_layers).lower(),
    }
    if image_b64:
        data["image_b64"] = image_b64
    if page_id:
        data["page_id"] = page_id
    r = await _post_multipart(
        base_url=settings.vision_url,
        path="/v1/clean",
        data=data,
        files=_file_tuple(image_bytes, filename),
    )
    _raise_from_response(r)
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype == "application/json":
        return r.json()
    # Pass through PNG + clean status headers from vision.
    headers = {}
    for key in ("X-Clean", "X-Editor-Regions", "X-Editor-Bubble-Ids"):
        if key in r.headers:
            headers[key] = r.headers[key]
    return Response(content=r.content, media_type="image/png", headers=headers)

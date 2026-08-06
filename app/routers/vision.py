from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from ..vision_client import vision_bubbles, vision_clean, vision_order, vision_pipeline

router = APIRouter(prefix="/api/vision", tags=["vision"])


async def _read_upload(
    file: UploadFile | None,
) -> tuple[Optional[bytes], Optional[str]]:
    if file is None:
        return None, None
    return await file.read(), file.filename or "page.png"


@router.post("/bubbles")
async def proxy_bubbles(
    file: UploadFile | None = File(None),
    image_b64: str | None = Form(None),
    page_id: str | None = Form(None),
):
    """Proxy to full vision ``POST /v1/bubbles`` (YOLO)."""
    if file is None and not image_b64:
        raise HTTPException(400, "Provide multipart `file` or form field `image_b64`")
    image_bytes, filename = await _read_upload(file)
    return await vision_bubbles(
        image_bytes=image_bytes,
        filename=filename,
        image_b64=image_b64,
        page_id=page_id,
    )


@router.post("/order")
async def proxy_order(
    file: UploadFile | None = File(None),
    image_b64: str | None = Form(None),
    page_id: str | None = Form(None),
    include_overlay: bool = Form(True),
):
    """Proxy to full vision ``POST /v1/order`` (YOLO)."""
    if file is None and not image_b64:
        raise HTTPException(400, "Provide multipart `file` or form field `image_b64`")
    image_bytes, filename = await _read_upload(file)
    return await vision_order(
        image_bytes=image_bytes,
        filename=filename,
        image_b64=image_b64,
        page_id=page_id,
        include_overlay=include_overlay,
    )


@router.post("/pipeline")
async def proxy_pipeline(
    file: UploadFile | None = File(None),
    image_b64: str | None = Form(None),
    page_id: str | None = Form(None),
    include_layers: bool = Form(False),
):
    """Proxy to full vision ``POST /v1/pipeline`` (YOLO + clean)."""
    if file is None and not image_b64:
        raise HTTPException(400, "Provide multipart `file` or form field `image_b64`")
    image_bytes, filename = await _read_upload(file)
    return await vision_pipeline(
        image_bytes=image_bytes,
        filename=filename,
        image_b64=image_b64,
        page_id=page_id,
        include_layers=include_layers,
    )


@router.post("/clean")
async def proxy_clean(
    file: UploadFile | None = File(None),
    image_b64: str | None = Form(None),
    bubbles_json: str = Form(...),
    page_id: str | None = Form(None),
    as_base64: bool = Form(False),
    include_layers: bool = Form(False),
):
    """Proxy to light vision ``POST /v1/clean`` (no YOLO)."""
    if file is None and not image_b64:
        raise HTTPException(400, "Provide multipart `file` or form field `image_b64`")
    image_bytes, filename = await _read_upload(file)
    result = await vision_clean(
        image_bytes=image_bytes,
        filename=filename,
        image_b64=image_b64,
        bubbles_json=bubbles_json,
        page_id=page_id,
        as_base64=as_base64,
        include_layers=include_layers,
    )
    if isinstance(result, Response):
        return result
    return result

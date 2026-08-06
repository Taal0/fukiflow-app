from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..storage import find_page_file

router = APIRouter(prefix="/api", tags=["assets"])

_CACHE = "public, max-age=31536000, immutable"


@router.get("/assets/{chapter}/{stem}/{name}")
def get_asset(chapter: str, stem: str, name: str):
    """Serve a page-level file (e.g. page_1.png, page_1-orig.png)."""
    return _serve(chapter, stem, name)


@router.get("/assets/{chapter}/{stem}/{subdir}/{name}")
def get_asset_nested(chapter: str, stem: str, subdir: str, name: str):
    """Serve layer crops under <stem>-layers-crop/."""
    return _serve(chapter, stem, f"{subdir}/{name}")


def _serve(chapter: str, stem: str, rel: str) -> FileResponse:
    # Prevent path traversal.
    if ".." in chapter or ".." in stem or ".." in rel:
        raise HTTPException(400, "invalid path")
    path = find_page_file(chapter, stem, rel)
    if path is None:
        raise HTTPException(404, f"asset not found: {rel}")
    media = "image/png" if path.suffix.lower() == ".png" else "application/octet-stream"
    return FileResponse(path, media_type=media, headers={"Cache-Control": _CACHE})

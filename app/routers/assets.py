from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..settings import get_settings

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
    settings = get_settings()
    # Prevent path traversal.
    if ".." in chapter or ".." in stem or ".." in rel:
        raise HTTPException(400, "invalid path")
    path = (settings.seed_dir / chapter / stem / rel).resolve()
    root = (settings.seed_dir / chapter / stem).resolve()
    if not str(path).startswith(str(root)):
        raise HTTPException(400, "invalid path")
    if not path.is_file():
        raise HTTPException(404, f"asset not found: {rel}")
    media = "image/png" if path.suffix.lower() == ".png" else "application/octet-stream"
    return FileResponse(path, media_type=media, headers={"Cache-Control": _CACHE})

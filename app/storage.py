"""Resolve chapter asset roots (seed vs user upload)."""

from __future__ import annotations

from pathlib import Path

from .settings import get_settings


def data_root_for(source_type: str | None) -> Path:
    """Return the filesystem root for a chapter's assets."""
    settings = get_settings()
    if (source_type or "seed") == "upload":
        return settings.upload_dir
    return settings.seed_dir


def chapter_dir(slug: str, source_type: str | None) -> Path:
    return data_root_for(source_type) / slug


def asset_path(rel_path: str, source_type: str | None) -> Path:
    """``rel_path`` is relative to the chapter data root (e.g. ``slug/stem/stem.png``)."""
    return data_root_for(source_type) / rel_path


def find_page_file(chapter: str, stem: str, rel: str) -> Path | None:
    """Locate a page file under upload_dir or seed_dir (upload preferred)."""
    settings = get_settings()
    for root in (settings.upload_dir, settings.seed_dir):
        path = (root / chapter / stem / rel).resolve()
        base = (root / chapter / stem).resolve()
        try:
            path.relative_to(base)
        except ValueError:
            continue
        if path.is_file():
            return path
    return None

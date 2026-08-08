from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChapterOut(BaseModel):
    id: int
    slug: str
    title: str
    page_count: int
    source_type: str = "seed"
    created_at: Optional[str] = None
    cover_url: Optional[str] = None
    done_count: int = 0
    bubble_count: int = 0
    processing_count: int = 0
    error_count: int = 0


class PageSummary(BaseModel):
    id: int
    page_no: int
    stem: str
    width: int
    height: int
    thumb_url: Optional[str] = None
    clean_status: str
    typeset_status: str
    bubble_count: int
    done_count: int = 0
    translated_count: int = 0
    processing_status: str = "ready"
    error_message: Optional[str] = None
    flag_count: int = 0


class PageProgress(BaseModel):
    page_id: int
    page_no: int
    bubble_count: int
    done_count: int
    translated_count: int = 0


class ChapterProgress(BaseModel):
    slug: str
    pages: List[PageProgress]


class PageEditOut(BaseModel):
    rev: int
    edit: Dict[str, Any]
    author: Optional[str] = Field(
        default=None,
        description="Last writer identity — roller seam; None for single-user letterer.",
    )


class PageEditPut(BaseModel):
    rev: int
    edit: Dict[str, Any]
    user_id: Optional[str] = Field(
        default=None,
        description="Optional roller / translator identity; stored as page_edit.author.",
    )


class PageMeta(BaseModel):
    id: int
    chapter_id: int
    chapter_slug: str
    page_no: int
    stem: str
    width: int
    height: int
    dpi: float
    clean_status: str
    typeset_status: str
    bubble_count: int
    editor_bubble_count: int


class AssetUrls(BaseModel):
    original_url: Optional[str] = None
    cleaned_url: Optional[str] = None
    compose_url: Optional[str] = None
    detect_url: Optional[str] = None


class LayerAssetOut(BaseModel):
    layer_id: str
    kind: str
    bubble_id: Optional[int] = None
    url: str
    offset_x: int
    offset_y: int
    width: int
    height: int


class PageDetail(BaseModel):
    page: PageMeta
    doc: Dict[str, Any]
    assets: AssetUrls
    layer_assets: List[LayerAssetOut] = Field(default_factory=list)
    edit: Optional[Dict[str, Any]] = None
    edit_rev: int = 0
    edit_author: Optional[str] = None


class TypesetTextIn(BaseModel):
    bubble_id: int
    text: str = ""


class TypesetRequest(BaseModel):
    texts: List[TypesetTextIn]
    font: Optional[str] = None
    overrides: Optional[Dict[str, Any]] = None


class ComposeRequest(BaseModel):
    doc: Dict[str, Any]
    off: Optional[List[str]] = None
    only_kind: Optional[str] = None
    font: Optional[str] = None

"""Verify translator regions against YOLO on padded crops; promote matches."""

from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from PIL import Image
from pydantic import BaseModel, Field

from vision.document import Layer

from ..db import get_db
from ..edits import (
    ConflictError,
    empty_edit,
    load_page_edit,
    upsert_page_edit,
    validate_edit,
)
from ..settings import get_settings
from ..storage import chapter_dir
from ..vision_client import vision_bubbles, vision_clean
from .pages import _asset_url
from .upload import _crop_clean_layer, _rasterize_clean_layer

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["verify"])

# Include verifying: UI may persist that status before POST returns.
# Do not include unconfirmed — already YOLO-failed; client resets to
# unclassified on text/bbox edit when a retry is intended.
_PENDING_STATUSES = frozenset({"unclassified", "verifying"})
_MAX_EDIT_RETRIES = 3


class VerifyRegionsResponse(BaseModel):
    rev: int
    regions: List[Dict[str, Any]]
    promoted: List[Dict[str, Any]] = Field(default_factory=list)
    additional_bubbles: List[Dict[str, Any]] = Field(default_factory=list)
    layer_assets: List[Dict[str, Any]] = Field(default_factory=list)
    edit: Dict[str, Any]


def iou(box_a: List[float], box_b: List[float]) -> float:
    x0 = max(box_a[0], box_b[0])
    y0 = max(box_a[1], box_b[1])
    x1 = min(box_a[2], box_b[2])
    y1 = min(box_a[3], box_b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_a = max(0.0, (box_a[2] - box_a[0]) * (box_a[3] - box_a[1]))
    area_b = max(0.0, (box_b[2] - box_b[0]) * (box_b[3] - box_b[1]))
    return inter / (area_a + area_b - inter + 1e-6)


def padded_crop_box(
    bbox: List[float],
    page_w: int,
    page_h: int,
    *,
    pad_ratio: float = 0.15,
    min_pad_px: float = 80.0,
) -> Tuple[int, int, int, int]:
    """Expand bbox with dynamic padding; clamp to page. Returns crop box."""
    x0, y0, x1, y1 = (float(v) for v in bbox)
    bw = max(1.0, x1 - x0)
    bh = max(1.0, y1 - y0)
    pad_x = max(pad_ratio * bw, min_pad_px)
    pad_y = max(pad_ratio * bh, min_pad_px)
    crop_x0 = max(0, int(round(x0 - pad_x)))
    crop_y0 = max(0, int(round(y0 - pad_y)))
    crop_x1 = min(page_w, int(round(x1 + pad_x)))
    crop_y1 = min(page_h, int(round(y1 + pad_y)))
    if crop_x1 <= crop_x0:
        crop_x1 = min(page_w, crop_x0 + 1)
    if crop_y1 <= crop_y0:
        crop_y1 = min(page_h, crop_y0 + 1)
    return crop_x0, crop_y0, crop_x1, crop_y1


def _offset_detection(
    det: Dict[str, Any], ox: int, oy: int
) -> Dict[str, Any]:
    bbox = det.get("bbox") or [0, 0, 0, 0]
    poly = det.get("polygon") or []
    return {
        **det,
        "bbox": [
            int(round(float(bbox[0]) + ox)),
            int(round(float(bbox[1]) + oy)),
            int(round(float(bbox[2]) + ox)),
            int(round(float(bbox[3]) + oy)),
        ],
        "polygon": [
            [float(p[0]) + ox, float(p[1]) + oy]
            for p in poly
            if isinstance(p, (list, tuple)) and len(p) >= 2
        ],
    }


def _next_bubble_id(bubbles: List[Dict[str, Any]]) -> int:
    max_id = -1
    for b in bubbles:
        try:
            max_id = max(max_id, int(b["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return max_id + 1


def _bubble_from_detection(
    det: Dict[str, Any],
    *,
    bubble_id: int,
    reading_order: Optional[int] = None,
) -> Dict[str, Any]:
    bbox = det["bbox"]
    return {
        "id": bubble_id,
        "polygon": det.get("polygon") or [
            [bbox[0], bbox[1]],
            [bbox[2], bbox[1]],
            [bbox[2], bbox[3]],
            [bbox[0], bbox[3]],
        ],
        "bbox": [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])],
        "confidence": float(det.get("confidence") or 0.0),
        "cls": int(det.get("cls") or 0),
        "panel_id": None,
        "assign_mode": "unassigned",
        "reading_order": reading_order,
        "order_source": "manual",
        "order_confidence": float(det.get("confidence") or 0.0),
        "clean_status": "unknown",
    }


def _merge_clean_patch(
    existing_cleaned: Image.Image,
    cleaned_from_vision: Image.Image,
    bubbles: List[Dict[str, Any]],
) -> Image.Image:
    """Paste vision-cleaned bubble regions onto the existing cleaned page."""
    base = existing_cleaned.convert("RGB")
    patch = cleaned_from_vision.convert("RGB")
    if patch.size != base.size:
        patch = patch.resize(base.size, Image.Resampling.LANCZOS)
    out = base.copy()
    for b in bubbles:
        bb = b.get("bbox")
        if not isinstance(bb, (list, tuple)) or len(bb) != 4:
            continue
        x0, y0, x1, y1 = (int(v) for v in bb)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(base.size[0], x1), min(base.size[1], y1)
        if x1 <= x0 or y1 <= y0:
            continue
        region = patch.crop((x0, y0, x1, y1))
        out.paste(region, (x0, y0))
    return out


def _write_one_layer_crop(
    conn,
    *,
    page_id: int,
    page_dir: Path,
    stem: str,
    slug: str,
    layer: Layer,
    page_w: int,
    page_h: int,
) -> Optional[Dict[str, Any]]:
    crop_dir = page_dir / f"{stem}-layers-crop"
    crop_dir.mkdir(exist_ok=True)
    full = _rasterize_clean_layer(layer, (page_w, page_h))
    if full.getbbox() is None:
        return None
    cropped, ox, oy = _crop_clean_layer(full, tuple(layer.bbox))
    dest = crop_dir / f"{layer.id}.png"
    cropped.save(dest)
    rel = f"{slug}/{stem}/{stem}-layers-crop/{layer.id}.png"
    conn.execute(
        "DELETE FROM layer_asset WHERE page_id = ? AND layer_id = ?",
        (page_id, layer.id),
    )
    conn.execute(
        """
        INSERT INTO layer_asset (
            page_id, layer_id, kind, bubble_id, rel_path,
            offset_x, offset_y, width, height
        ) VALUES (?, ?, 'clean', ?, ?, ?, ?, ?, ?)
        """,
        (
            page_id,
            layer.id,
            layer.bubble_id,
            rel,
            ox,
            oy,
            cropped.size[0],
            cropped.size[1],
        ),
    )
    return {
        "layer_id": layer.id,
        "kind": "clean",
        "bubble_id": layer.bubble_id,
        "url": _asset_url(slug, stem, rel),
        "offset_x": ox,
        "offset_y": oy,
        "width": cropped.size[0],
        "height": cropped.size[1],
    }


def _apply_region_updates(
    edit: Dict[str, Any],
    *,
    region_updates: Dict[str, Dict[str, Any]],
    bubble_texts: Dict[int, str],
    order_replacements: Dict[str, str],
) -> Dict[str, Any]:
    """Merge verify results into current edit (partial update)."""
    regions = []
    for r in edit.get("regions") or []:
        if not isinstance(r, dict):
            continue
        rid = r.get("id")
        if rid in region_updates:
            regions.append({**r, **region_updates[rid]})
        else:
            regions.append(dict(r))

    bubbles = dict(edit.get("bubbles") or {})
    for bid, text in bubble_texts.items():
        key = str(bid)
        entry = dict(bubbles.get(key) or {})
        entry["text"] = text
        bubbles[key] = entry

    order = list(edit.get("order") or [])
    if order_replacements:
        new_order: List[str] = []
        seen: set[str] = set()
        for token in order:
            repl = order_replacements.get(token, token)
            if repl in seen:
                continue
            seen.add(repl)
            new_order.append(repl)
        for repl in order_replacements.values():
            if repl not in seen:
                seen.add(repl)
                new_order.append(repl)
        order = new_order

    return {
        **edit,
        "regions": regions,
        "bubbles": bubbles,
        "order": order,
    }


@router.post(
    "/pages/{page_id}/verify-regions",
    response_model=VerifyRegionsResponse,
)
async def verify_regions(page_id: int) -> VerifyRegionsResponse:
    settings = get_settings()
    verify_conf = settings.verify_conf
    pad_ratio = settings.verify_padding
    min_pad = settings.verify_pad_min_px
    iou_min = settings.verify_iou_min
    additional_conf = settings.verify_additional_conf

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
        loaded = load_page_edit(conn, page_id)

    slug = row["chapter_slug"]
    stem = row["stem"]
    source_type = row["source_type"] or "seed"
    page_dir = chapter_dir(slug, source_type) / stem
    doc_path = page_dir / f"{stem}-doc.json"
    orig_path = page_dir / f"{stem}-orig.png"
    cleaned_path = page_dir / f"{stem}.png"

    if not doc_path.is_file():
        raise HTTPException(500, f"PageDoc missing on disk: {doc_path}")
    if not orig_path.is_file():
        raise HTTPException(500, f"original image missing: {orig_path}")

    try:
        doc_raw = json.loads(doc_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"invalid PageDoc: {e}") from e

    if loaded is None:
        edit = empty_edit(rev=0)
    else:
        edit, _, _ = loaded

    pending = [
        r
        for r in (edit.get("regions") or [])
        if isinstance(r, dict)
        and r.get("status") in _PENDING_STATUSES
        and (r.get("kind") or "bubble") == "bubble"
        and isinstance(r.get("text"), str)
        and r["text"].strip()
    ]
    if not pending:
        return VerifyRegionsResponse(
            rev=int(edit.get("rev") or 0),
            regions=list(edit.get("regions") or []),
            edit=edit,
        )

    try:
        orig_img = Image.open(orig_path).convert("RGB")
    except Exception as e:
        raise HTTPException(500, f"cannot read original: {e}") from e

    page_w, page_h = orig_img.size
    orig_bytes = orig_path.read_bytes()

    region_updates: Dict[str, Dict[str, Any]] = {}
    bubble_texts: Dict[int, str] = {}
    order_replacements: Dict[str, str] = {}
    to_promote: List[Tuple[str, Dict[str, Any], str]] = []  # rid, det, text
    additional_dets: List[Dict[str, Any]] = []
    seen_det_keys: set[Tuple[int, int, int, int]] = set()

    for region in pending:
        rid = region["id"]
        bbox = [float(v) for v in region["bbox"]]
        text = region["text"]
        crop_box = padded_crop_box(
            bbox,
            page_w,
            page_h,
            pad_ratio=pad_ratio,
            min_pad_px=min_pad,
        )
        cx0, cy0, cx1, cy1 = crop_box
        crop = orig_img.crop((cx0, cy0, cx1, cy1))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        crop_bytes = buf.getvalue()

        try:
            result = await vision_bubbles(
                image_bytes=crop_bytes,
                filename=f"{stem}-{rid}.png",
                page_id=f"{stem}-{rid}",
                conf=verify_conf,
            )
        except HTTPException:
            region_updates[rid] = {
                "status": "unconfirmed",
                "confidence": None,
            }
            continue
        except Exception as e:
            log.warning("verify vision_bubbles failed for %s: %s", rid, e)
            region_updates[rid] = {
                "status": "unconfirmed",
                "confidence": None,
            }
            continue

        dets_raw = result.get("bubbles") or []
        dets = [_offset_detection(d, cx0, cy0) for d in dets_raw if isinstance(d, dict)]

        best: Optional[Dict[str, Any]] = None
        best_iou = 0.0
        for d in dets:
            score = iou(bbox, [float(v) for v in d["bbox"]])
            if score > best_iou:
                best_iou = score
                best = d

        best_conf = float(best["confidence"]) if best else 0.0
        matched = (
            best is not None
            and best_conf >= verify_conf
            and best_iou >= iou_min
        )

        if matched and best is not None:
            key = tuple(int(v) for v in best["bbox"])
            seen_det_keys.add(key)
            to_promote.append((rid, best, text))
            region_updates[rid] = {
                "status": "confirmed",
                "confidence": best_conf,
            }
            for d in dets:
                if d is best:
                    continue
                d_conf = float(d.get("confidence") or 0.0)
                if d_conf < additional_conf:
                    continue
                d_key = tuple(int(v) for v in d["bbox"])
                if d_key in seen_det_keys:
                    continue
                seen_det_keys.add(d_key)
                additional_dets.append(d)
        else:
            region_updates[rid] = {
                "status": "unconfirmed",
                "confidence": best_conf if best else 0.0,
            }

    bubbles_list = list(doc_raw.get("bubbles") or [])
    next_id = _next_bubble_id(bubbles_list)
    promoted_bubbles: List[Dict[str, Any]] = []
    additional_bubbles: List[Dict[str, Any]] = []
    new_bubble_dicts: List[Dict[str, Any]] = []

    for rid, det, text in to_promote:
        bubble = _bubble_from_detection(det, bubble_id=next_id)
        next_id += 1
        region_updates[rid]["bubble_id"] = bubble["id"]
        bubble_texts[bubble["id"]] = text
        order_replacements[rid] = f"b:{bubble['id']}"
        promoted_bubbles.append(bubble)
        new_bubble_dicts.append(bubble)

    for det in additional_dets:
        bubble = _bubble_from_detection(det, bubble_id=next_id)
        next_id += 1
        additional_bubbles.append(bubble)
        new_bubble_dicts.append(bubble)

    new_layers: List[Dict[str, Any]] = []
    layer_asset_outs: List[Dict[str, Any]] = []

    if new_bubble_dicts:
        try:
            clean_result = await vision_clean(
                image_bytes=orig_bytes,
                filename=f"{stem}-orig.png",
                bubbles_json=json.dumps(new_bubble_dicts),
                page_id=stem,
                as_base64=True,
                include_layers=True,
            )
        except Exception as e:
            log.warning("verify vision_clean failed: %s", e)
            clean_result = None

        if isinstance(clean_result, dict):
            for layer in clean_result.get("clean_layers") or []:
                if isinstance(layer, dict):
                    new_layers.append(layer)
            cleaned_b64 = clean_result.get("cleaned_image")
            if cleaned_b64 and cleaned_path.is_file():
                try:
                    vision_img = Image.open(
                        io.BytesIO(base64.b64decode(cleaned_b64))
                    )
                    existing = Image.open(cleaned_path)
                    layer_bids = {
                        int(L["bubble_id"])
                        for L in new_layers
                        if L.get("bubble_id") is not None
                    }
                    filled = [
                        b for b in new_bubble_dicts if int(b["id"]) in layer_bids
                    ]
                    if filled:
                        merged = _merge_clean_patch(existing, vision_img, filled)
                        buf = io.BytesIO()
                        merged.save(buf, format="PNG")
                        cleaned_path.write_bytes(buf.getvalue())
                except Exception as e:
                    log.warning("verify clean patch failed: %s", e)

        layer_bids = {
            int(L["bubble_id"])
            for L in new_layers
            if isinstance(L, dict) and L.get("bubble_id") is not None
        }
        for b in new_bubble_dicts:
            bid = int(b["id"])
            if clean_result is None:
                b["clean_status"] = "unknown"
            elif bid in layer_bids:
                b["clean_status"] = "filled"
            else:
                b["clean_status"] = "editor"

        bubbles_list.extend(new_bubble_dicts)
        layers_list = list(doc_raw.get("layers") or [])
        layers_list.extend(new_layers)
        layers_list.sort(
            key=lambda L: (
                int(L.get("z") or 0) if isinstance(L, dict) else 0,
                str(L.get("id") or "") if isinstance(L, dict) else "",
            )
        )
        doc_raw["bubbles"] = bubbles_list
        doc_raw["layers"] = layers_list

        flags = dict(doc_raw.get("flags") or {})
        editor_flag = list(flags.get("editor_bubble_ids") or [])
        for b in new_bubble_dicts:
            if b.get("clean_status") == "editor":
                editor_flag.append(int(b["id"]))
        flags["editor_bubble_ids"] = sorted(set(int(x) for x in editor_flag))
        if any(b.get("clean_status") == "editor" for b in bubbles_list):
            flags["clean"] = "partial"
        doc_raw["flags"] = flags

        # Persist PageDoc + layer crops
        tmp = doc_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(doc_raw, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(doc_path)

        page_w_doc = int((doc_raw.get("source") or {}).get("width") or page_w)
        page_h_doc = int((doc_raw.get("source") or {}).get("height") or page_h)

        with get_db() as conn:
            for layer_raw in new_layers:
                if not isinstance(layer_raw, dict) or layer_raw.get("kind") != "clean":
                    continue
                try:
                    layer = Layer.model_validate(layer_raw)
                except Exception:
                    continue
                asset = _write_one_layer_crop(
                    conn,
                    page_id=page_id,
                    page_dir=page_dir,
                    stem=stem,
                    slug=slug,
                    layer=layer,
                    page_w=page_w_doc,
                    page_h=page_h_doc,
                )
                if asset:
                    layer_asset_outs.append(asset)

            editor_count = len(flags.get("editor_bubble_ids") or [])
            conn.execute(
                """
                UPDATE page SET bubble_count=?, editor_bubble_count=?,
                    clean_status=?
                WHERE id=?
                """,
                (
                    len(bubbles_list),
                    editor_count,
                    flags.get("clean") or row["clean_status"],
                    page_id,
                ),
            )

    # Persist edit with conflict retry
    saved_edit = edit
    for attempt in range(_MAX_EDIT_RETRIES):
        with get_db() as conn:
            loaded = load_page_edit(conn, page_id)
            if loaded is None:
                current = empty_edit(rev=0)
                expected_rev = 0
            else:
                current, expected_rev, _ = loaded
            merged = _apply_region_updates(
                current,
                region_updates=region_updates,
                bubble_texts=bubble_texts,
                order_replacements=order_replacements,
            )
            try:
                validated = validate_edit(merged)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            try:
                saved_edit, _ = upsert_page_edit(
                    conn,
                    page_id,
                    validated,
                    expected_rev=expected_rev,
                    author="verify",
                )
                break
            except ConflictError:
                if attempt + 1 >= _MAX_EDIT_RETRIES:
                    raise HTTPException(
                        409,
                        {"message": "edit conflict during verify", "current_rev": expected_rev},
                    )
                continue

    return VerifyRegionsResponse(
        rev=int(saved_edit.get("rev") or 0),
        regions=list(saved_edit.get("regions") or []),
        promoted=promoted_bubbles,
        additional_bubbles=additional_bubbles,
        layer_assets=layer_asset_outs,
        edit=saved_edit,
    )

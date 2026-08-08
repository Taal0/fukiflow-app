"""PageEdit overlay: validate, persist helpers, and seed→working merge.

The seed PageDoc is immutable. All letterer / translator edits live in a
separate overlay stored in SQLite (`page_edit`). Client and server must
produce the same working doc from `merge_page_edit(seed, edit)`.

``bubbles[<id>].text`` is the shared write surface for letterer and
translator UI — both persist through ``PUT /api/pages/{id}/edit``.

Translator-only overlay keys ``regions`` / ``order`` live in the edit JSON;
``regions`` are never injected into the PageDoc (compose/typeset unchanged).
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, List, Optional, Tuple

# Overlay fields that belong on a bubble edit entry.
_BUBBLE_KEYS = frozenset(
    {
        "text",
        "font",
        "font_px",
        "font_scale",
        "leading",
        "align",
        "frame",
        "layers_off",
        "done",
        "inset",
        # Letterer opted into typeset on a clean_status=editor bubble.
        "typeset_try",
    }
)

_REGION_KEYS = frozenset(
    {"id", "bbox", "text", "kind", "status", "confidence", "bubble_id"}
)
_REGION_STATUS_OK = frozenset(
    {"unclassified", "verifying", "confirmed", "unconfirmed"}
)
_REGION_KIND_OK = frozenset({"bubble", "sfx", "other"})
_REGION_ID_RE = re.compile(r"^r-[A-Za-z0-9_-]{2,32}$")
_ORDER_BUBBLE_RE = re.compile(r"^b:(\d+)$")

_ALIGN_OK = frozenset({"left", "center", "right"})
_PAINT_MODES = frozenset({"fill", "restore"})
_INSET_MIN = 0
_INSET_MAX = 64
_PAINT_Z_BASE = 150


def empty_edit(*, rev: int = 0) -> Dict[str, Any]:
    return {
        "rev": rev,
        "page": {},
        "bubbles": {},
        "paint": [],
        "regions": [],
        "order": [],
        "removed_bubbles": [],
    }


def _removed_bubble_set(edit: Dict[str, Any] | None) -> set[int]:
    if not edit:
        return set()
    out: set[int] = set()
    for x in edit.get("removed_bubbles") or []:
        try:
            bid = int(x)
        except (TypeError, ValueError):
            continue
        if bid >= 0:
            out.add(bid)
    return out


def count_done(edit: Dict[str, Any] | None) -> int:
    if not edit:
        return 0
    removed = _removed_bubble_set(edit)
    bubbles = edit.get("bubbles") or {}
    n = 0
    for key, be in bubbles.items():
        if not isinstance(be, dict) or not be.get("done"):
            continue
        try:
            bid = int(key)
        except (TypeError, ValueError):
            continue
        if bid in removed:
            continue
        n += 1
    return n


def count_translated(edit: Dict[str, Any] | None) -> int:
    """Count overlay texts with non-empty strip — bubbles + regions."""
    if not edit:
        return 0
    removed = _removed_bubble_set(edit)
    n = 0
    for key, be in (edit.get("bubbles") or {}).items():
        if not isinstance(be, dict) or not isinstance(be.get("text"), str):
            continue
        if not be["text"].strip():
            continue
        try:
            bid = int(key)
        except (TypeError, ValueError):
            continue
        if bid in removed:
            continue
        n += 1
    for region in edit.get("regions") or []:
        if (
            isinstance(region, dict)
            and isinstance(region.get("text"), str)
            and region["text"].strip()
        ):
            n += 1
    return n

def layers_off_list(edit: Dict[str, Any] | None) -> List[str]:
    """Flatten per-bubble layers_off into a compose `off` list."""
    if not edit:
        return []
    out: List[str] = []
    seen: set[str] = set()
    for be in (edit.get("bubbles") or {}).values():
        if not isinstance(be, dict):
            continue
        for lid in be.get("layers_off") or []:
            if isinstance(lid, str) and lid not in seen:
                seen.add(lid)
                out.append(lid)
    return out


def validate_edit(raw: Any) -> Dict[str, Any]:
    """Normalize and validate a PageEdit overlay. Raises ValueError on bad input."""
    if raw is None:
        return empty_edit()
    if not isinstance(raw, dict):
        raise ValueError("edit must be a JSON object")

    rev = raw.get("rev", 0)
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 0:
        raise ValueError("rev must be a non-negative integer")

    page_in = raw.get("page") or {}
    if not isinstance(page_in, dict):
        raise ValueError("page must be an object")
    page: Dict[str, Any] = {}
    if "font" in page_in and page_in["font"] is not None:
        if not isinstance(page_in["font"], str) or not page_in["font"]:
            raise ValueError("page.font must be a non-empty string")
        page["font"] = page_in["font"]

    bubbles_in = raw.get("bubbles") or {}
    if not isinstance(bubbles_in, dict):
        raise ValueError("bubbles must be an object")
    bubbles: Dict[str, Any] = {}
    for key, be in bubbles_in.items():
        bid = _parse_bubble_id(key)
        if not isinstance(be, dict):
            raise ValueError(f"bubbles[{key}] must be an object")
        entry = _validate_bubble_edit(be, key)
        if entry:
            bubbles[str(bid)] = entry

    paint_in = raw.get("paint") or []
    if not isinstance(paint_in, list):
        raise ValueError("paint must be an array")
    paint = [_validate_stroke(s, i) for i, s in enumerate(paint_in)]

    regions_in = raw.get("regions") or []
    if not isinstance(regions_in, list):
        raise ValueError("regions must be an array")
    regions = [_validate_region(r, i) for i, r in enumerate(regions_in)]
    region_ids = {r["id"] for r in regions}
    if len(region_ids) != len(regions):
        raise ValueError("regions ids must be unique")

    removed_bubbles = _validate_removed_bubbles(raw.get("removed_bubbles"))
    removed_set = set(removed_bubbles)

    order_in = raw.get("order") or []
    if not isinstance(order_in, list):
        raise ValueError("order must be an array")
    order = _validate_order(order_in, region_ids, removed_set)

    return {
        "rev": rev,
        "page": page,
        "bubbles": bubbles,
        "paint": paint,
        "regions": regions,
        "order": order,
        "removed_bubbles": removed_bubbles,
    }


def _parse_bubble_id(key: Any) -> int:
    try:
        bid = int(key)
    except (TypeError, ValueError) as e:
        raise ValueError(f"bubble id must be an integer key, got {key!r}") from e
    if bid < 0:
        raise ValueError(f"bubble id must be non-negative, got {bid}")
    return bid


def _validate_bubble_edit(be: Dict[str, Any], key: str) -> Dict[str, Any]:
    unknown = set(be) - _BUBBLE_KEYS
    if unknown:
        raise ValueError(f"bubbles[{key}] unknown fields: {sorted(unknown)}")

    out: Dict[str, Any] = {}
    if "text" in be:
        if not isinstance(be["text"], str):
            raise ValueError(f"bubbles[{key}].text must be a string")
        out["text"] = be["text"]
    if "font" in be and be["font"] is not None:
        if not isinstance(be["font"], str) or not be["font"]:
            raise ValueError(f"bubbles[{key}].font must be a non-empty string")
        out["font"] = be["font"]
    if "font_px" in be and be["font_px"] is not None:
        px = be["font_px"]
        if not isinstance(px, (int, float)) or isinstance(px, bool) or px <= 0:
            raise ValueError(f"bubbles[{key}].font_px must be a positive number")
        out["font_px"] = float(px)
    if "font_scale" in be and be["font_scale"] is not None:
        sc = be["font_scale"]
        if not isinstance(sc, (int, float)) or isinstance(sc, bool) or sc <= 0:
            raise ValueError(f"bubbles[{key}].font_scale must be a positive number")
        out["font_scale"] = float(sc)
    if "leading" in be and be["leading"] is not None:
        ld = be["leading"]
        if not isinstance(ld, (int, float)) or isinstance(ld, bool) or ld <= 0:
            raise ValueError(f"bubbles[{key}].leading must be a positive number")
        out["leading"] = float(ld)
    if "align" in be and be["align"] is not None:
        if be["align"] not in _ALIGN_OK:
            raise ValueError(f"bubbles[{key}].align must be one of {sorted(_ALIGN_OK)}")
        out["align"] = be["align"]
    if "frame" in be and be["frame"] is not None:
        out["frame"] = _validate_frame(be["frame"], key)
    if "layers_off" in be and be["layers_off"] is not None:
        lo = be["layers_off"]
        if not isinstance(lo, list) or any(not isinstance(x, str) for x in lo):
            raise ValueError(f"bubbles[{key}].layers_off must be a string array")
        # Dedupe, preserve order
        seen: set[str] = set()
        cleaned: List[str] = []
        for lid in lo:
            if lid not in seen:
                seen.add(lid)
                cleaned.append(lid)
        out["layers_off"] = cleaned
    if "done" in be and be["done"] is not None:
        if not isinstance(be["done"], bool):
            raise ValueError(f"bubbles[{key}].done must be a boolean")
        if be["done"]:
            out["done"] = True
    if "inset" in be and be["inset"] is not None:
        inset = be["inset"]
        if (
            not isinstance(inset, (int, float))
            or isinstance(inset, bool)
            or inset < _INSET_MIN
            or inset > _INSET_MAX
        ):
            raise ValueError(
                f"bubbles[{key}].inset must be an integer {_INSET_MIN}–{_INSET_MAX}"
            )
        out["inset"] = int(round(inset))
    if "typeset_try" in be and be["typeset_try"] is not None:
        if not isinstance(be["typeset_try"], bool):
            raise ValueError(f"bubbles[{key}].typeset_try must be a boolean")
        if be["typeset_try"]:
            out["typeset_try"] = True
    return out


def _validate_frame(frame: Any, key: str) -> List[float]:
    if (
        not isinstance(frame, (list, tuple))
        or len(frame) != 4
        or any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in frame)
    ):
        raise ValueError(f"bubbles[{key}].frame must be [x0,y0,x1,y1]")
    x0, y0, x1, y1 = (float(v) for v in frame)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"bubbles[{key}].frame must have positive area")
    return [x0, y0, x1, y1]


def _validate_bbox(bbox: Any, label: str) -> List[float]:
    if (
        not isinstance(bbox, (list, tuple))
        or len(bbox) != 4
        or any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in bbox)
    ):
        raise ValueError(f"{label} must be [x1,y1,x2,y2]")
    x1, y1, x2, y2 = (float(v) for v in bbox)
    if x1 < 0 or y1 < 0 or x2 < 0 or y2 < 0:
        raise ValueError(f"{label} must be non-negative")
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"{label} must have x2>x1 and y2>y1")
    return [x1, y1, x2, y2]


def _validate_region(region: Any, index: int) -> Dict[str, Any]:
    if not isinstance(region, dict):
        raise ValueError(f"regions[{index}] must be an object")
    unknown = set(region) - _REGION_KEYS
    if unknown:
        raise ValueError(f"regions[{index}] unknown fields: {sorted(unknown)}")

    rid = region.get("id")
    if not isinstance(rid, str) or not _REGION_ID_RE.match(rid):
        raise ValueError(
            f"regions[{index}].id must match {_REGION_ID_RE.pattern}"
        )

    bbox = _validate_bbox(region.get("bbox"), f"regions[{index}].bbox")

    text = region.get("text", "")
    if text is None:
        text = ""
    if not isinstance(text, str):
        raise ValueError(f"regions[{index}].text must be a string")

    # Legacy edits omit kind — treat as bubble (YOLO-verify eligible).
    kind = region.get("kind", "bubble")
    if kind is None:
        kind = "bubble"
    if kind not in _REGION_KIND_OK:
        raise ValueError(
            f"regions[{index}].kind must be one of {sorted(_REGION_KIND_OK)}"
        )

    status = region.get("status", "unclassified")
    if status not in _REGION_STATUS_OK:
        raise ValueError(
            f"regions[{index}].status must be one of {sorted(_REGION_STATUS_OK)}"
        )

    confidence = region.get("confidence", None)
    if confidence is not None:
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or confidence < 0
            or confidence > 1
        ):
            raise ValueError(
                f"regions[{index}].confidence must be null or a number 0..1"
            )
        confidence = float(confidence)

    bubble_id = region.get("bubble_id", None)
    if bubble_id is not None:
        if not isinstance(bubble_id, int) or isinstance(bubble_id, bool) or bubble_id < 0:
            raise ValueError(
                f"regions[{index}].bubble_id must be null or a non-negative integer"
            )

    out: Dict[str, Any] = {
        "id": rid,
        "bbox": bbox,
        "text": text,
        "kind": kind,
        "status": status,
        "confidence": confidence,
    }
    if bubble_id is not None:
        out["bubble_id"] = bubble_id
    return out


def _validate_removed_bubbles(raw: Any) -> List[int]:
    """Normalize removed_bubbles: non-neg ints, dedupe, preserve order."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("removed_bubbles must be an array")
    out: List[int] = []
    seen: set[int] = set()
    for i, item in enumerate(raw):
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(
                f"removed_bubbles[{i}] must be a non-negative integer"
            )
        if item < 0:
            raise ValueError(
                f"removed_bubbles[{i}] must be a non-negative integer"
            )
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _validate_order(
    order_in: List[Any],
    region_ids: set[str],
    removed_bubbles: set[int] | None = None,
) -> List[str]:
    """Validate order tokens; drop duplicates, orphaned r-*, and removed b:N."""
    removed = removed_bubbles or set()
    out: List[str] = []
    seen: set[str] = set()
    for i, token in enumerate(order_in):
        if not isinstance(token, str):
            raise ValueError(f"order[{i}] must be a string")
        if token in seen:
            continue
        m = _ORDER_BUBBLE_RE.match(token)
        if m:
            bid = int(m.group(1))
            if bid in removed:
                continue
            seen.add(token)
            out.append(token)
            continue
        if _REGION_ID_RE.match(token):
            if token not in region_ids:
                # Stale token after region delete — drop silently.
                continue
            seen.add(token)
            out.append(token)
            continue
        raise ValueError(
            f"order[{i}] must be 'b:<int>' or a region id (r-…), got {token!r}"
        )
    return out


def ordered_items(
    seed_doc: Dict[str, Any],
    edit: Dict[str, Any] | None,
) -> List[Tuple[str, str]]:
    """Unified reading order as ``(kind, id)`` pairs.

    ``kind`` is ``"bubble"`` (id = decimal string) or ``"region"`` (id = ``r-…``).
    Tokens in ``edit.order`` come first; remaining seed bubbles append by their
    seed ``reading_order``; remaining regions append in array order.
    """
    edit = edit or {}
    removed_set = _removed_bubble_set(edit)
    regions = [r for r in (edit.get("regions") or []) if isinstance(r, dict)]
    # YOLO-confirmed regions (with bubble_id) are represented by their promoted
    # bubble. Manual sfx/other stay confirmed without bubble_id and remain listed.
    active_regions = [
        r
        for r in regions
        if isinstance(r.get("id"), str)
        and not (
            r.get("status") == "confirmed" and r.get("bubble_id") is not None
        )
    ]
    region_ids = {r["id"] for r in active_regions}

    bubbles = list(seed_doc.get("bubbles") or [])
    bubble_ids: set[int] = set()
    for b in bubbles:
        try:
            bid = int(b["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if bid in removed_set:
            continue
        bubble_ids.add(bid)

    items: List[Tuple[str, str]] = []
    seen_bubbles: set[int] = set()
    seen_regions: set[str] = set()

    for token in edit.get("order") or []:
        if not isinstance(token, str):
            continue
        m = _ORDER_BUBBLE_RE.match(token)
        if m:
            bid = int(m.group(1))
            if bid in bubble_ids and bid not in seen_bubbles:
                seen_bubbles.add(bid)
                items.append(("bubble", str(bid)))
            continue
        if token in region_ids and token not in seen_regions:
            seen_regions.add(token)
            items.append(("region", token))

    remaining = []
    for b in bubbles:
        try:
            bid = int(b["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if bid in removed_set:
            continue
        if bid not in seen_bubbles:
            remaining.append(b)
    remaining.sort(
        key=lambda b: (
            b.get("reading_order") is None,
            b.get("reading_order") if b.get("reading_order") is not None else 0,
            int(b.get("id") or 0),
        )
    )
    for b in remaining:
        items.append(("bubble", str(int(b["id"]))))

    for r in active_regions:
        rid = r.get("id")
        if isinstance(rid, str) and rid in region_ids and rid not in seen_regions:
            seen_regions.add(rid)
            items.append(("region", rid))

    return items


def _apply_reading_order(
    doc: Dict[str, Any],
    edit: Dict[str, Any],
) -> None:
    """Renumber bubble ``reading_order`` from the unified order list (1..N).

    Regions occupy slots but are not written into the PageDoc. Only mutates
    when ``order`` or ``regions`` is non-empty so legacy overlays stay put.
    """
    has_order = bool(edit.get("order"))
    has_regions = bool(edit.get("regions"))
    if not has_order and not has_regions:
        return

    items = ordered_items(doc, edit)
    bubble_pos: Dict[int, int] = {}
    for i, (kind, ident) in enumerate(items, start=1):
        if kind == "bubble":
            bubble_pos[int(ident)] = i

    for bubble in doc.get("bubbles") or []:
        try:
            bid = int(bubble["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if bid in bubble_pos:
            bubble["reading_order"] = bubble_pos[bid]
            bubble["order_source"] = "manual"


def _validate_stroke(stroke: Any, index: int) -> Dict[str, Any]:
    if not isinstance(stroke, dict):
        raise ValueError(f"paint[{index}] must be an object")
    sid = stroke.get("id")
    if not isinstance(sid, str) or not sid:
        raise ValueError(f"paint[{index}].id must be a non-empty string")
    mode = stroke.get("mode")
    if mode not in _PAINT_MODES:
        raise ValueError(f"paint[{index}].mode must be one of {sorted(_PAINT_MODES)}")
    radius = stroke.get("radius_px")
    if not isinstance(radius, (int, float)) or isinstance(radius, bool) or radius <= 0:
        raise ValueError(f"paint[{index}].radius_px must be a positive number")
    points = stroke.get("points")
    if not isinstance(points, list) or not points:
        raise ValueError(f"paint[{index}].points must be a non-empty array")
    cleaned_pts: List[List[float]] = []
    for j, pt in enumerate(points):
        if (
            not isinstance(pt, (list, tuple))
            or len(pt) != 2
            or any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in pt)
        ):
            raise ValueError(f"paint[{index}].points[{j}] must be [x,y]")
        cleaned_pts.append([float(pt[0]), float(pt[1])])

    out: Dict[str, Any] = {
        "id": sid,
        "mode": mode,
        "radius_px": float(radius),
        "points": cleaned_pts,
    }
    if "bubble_id" in stroke and stroke["bubble_id"] is not None:
        if not isinstance(stroke["bubble_id"], int) or isinstance(stroke["bubble_id"], bool):
            raise ValueError(f"paint[{index}].bubble_id must be an integer")
        out["bubble_id"] = stroke["bubble_id"]
    if mode == "fill":
        color = stroke.get("color")
        if (
            not isinstance(color, (list, tuple))
            or len(color) != 3
            or any(
                not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0 or v > 255
                for v in color
            )
        ):
            raise ValueError(f"paint[{index}].color must be [r,g,b] 0–255")
        out["color"] = [int(color[0]), int(color[1]), int(color[2])]
    return out


def merge_page_edit(
    seed_doc: Dict[str, Any],
    edit: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Apply PageEdit overlay onto an immutable seed PageDoc.

    Applies:
    - bubble text overrides onto ``texts``
    - ``layers_off`` → ``layer.enabled = False``
    - ``inset`` → clean layer ``mask.inset``
    - ``paint`` strokes → ``kind=paint`` layers (z≈150)
    - page.font is returned via the edit itself (compose takes font separately)

    Font size / frame / leading do not rewrite seed layouts here — those go
    through typeset and land as text layers in the working doc the client
    (or a typeset pass) builds.
    """
    if not edit:
        return seed_doc

    doc = copy.deepcopy(seed_doc)
    removed_set = _removed_bubble_set(edit)
    if removed_set:
        kept_bubbles = []
        for b in doc.get("bubbles") or []:
            try:
                if int(b["id"]) in removed_set:
                    continue
            except (KeyError, TypeError, ValueError):
                pass
            kept_bubbles.append(b)
        doc["bubbles"] = kept_bubbles

        kept_texts = []
        for t in doc.get("texts") or []:
            try:
                if int(t["bubble_id"]) in removed_set:
                    continue
            except (KeyError, TypeError, ValueError):
                pass
            kept_texts.append(t)
        doc["texts"] = kept_texts

        kept_layers = []
        for layer in doc.get("layers") or []:
            bid = layer.get("bubble_id")
            if bid is not None:
                try:
                    if int(bid) in removed_set:
                        continue
                except (TypeError, ValueError):
                    pass
            kept_layers.append(layer)
        doc["layers"] = kept_layers

    bubbles_edit = edit.get("bubbles") or {}
    paint: List[Dict[str, Any]] = []
    for s in edit.get("paint") or []:
        if not isinstance(s, dict):
            continue
        bid = s.get("bubble_id")
        if bid is not None:
            try:
                if int(bid) in removed_set:
                    continue
            except (TypeError, ValueError):
                pass
        paint.append(s)

    texts = list(doc.get("texts") or [])
    by_id: Dict[int, Dict[str, Any]] = {}
    for t in texts:
        try:
            by_id[int(t["bubble_id"])] = t
        except (KeyError, TypeError, ValueError):
            continue

    off_ids: set[str] = set()
    inset_by_bubble: Dict[int, int] = {}
    for key, be in bubbles_edit.items():
        if not isinstance(be, dict):
            continue
        try:
            bid = int(key)
        except (TypeError, ValueError):
            continue
        if bid in removed_set:
            continue
        if "text" in be:
            if bid in by_id:
                by_id[bid]["text"] = be["text"]
            else:
                row = {"bubble_id": bid, "text": be["text"]}
                texts.append(row)
                by_id[bid] = row
        for lid in be.get("layers_off") or []:
            if isinstance(lid, str):
                off_ids.add(lid)
        if "inset" in be and be["inset"] is not None:
            inset_by_bubble[bid] = int(be["inset"])

    doc["texts"] = texts

    layers = list(doc.get("layers") or [])
    if off_ids or inset_by_bubble:
        for layer in layers:
            if layer.get("id") in off_ids:
                layer["enabled"] = False
            if layer.get("kind") == "clean":
                try:
                    bid = int(layer.get("bubble_id"))
                except (TypeError, ValueError):
                    continue
                if bid in inset_by_bubble:
                    mask = dict(layer.get("mask") or {})
                    mask["inset"] = inset_by_bubble[bid]
                    layer["mask"] = mask
        doc["layers"] = layers

    if paint:
        doc["layers"] = _merge_paint_layers(doc.get("layers") or [], paint, doc)

    _apply_reading_order(doc, edit)
    return doc


def _bubble_bbox(doc: Dict[str, Any], bubble_id: Optional[int]) -> List[int]:
    if bubble_id is None:
        src = doc.get("source") or {}
        return [0, 0, int(src.get("width") or 1), int(src.get("height") or 1)]
    for b in doc.get("bubbles") or []:
        try:
            if int(b.get("id")) == int(bubble_id):
                bb = b.get("bbox")
                if isinstance(bb, (list, tuple)) and len(bb) == 4:
                    return [int(bb[0]), int(bb[1]), int(bb[2]), int(bb[3])]
        except (TypeError, ValueError):
            continue
    src = doc.get("source") or {}
    return [0, 0, int(src.get("width") or 1), int(src.get("height") or 1)]


def _clean_enabled_for_bubble(
    layers: List[Dict[str, Any]],
    bubble_id: Optional[int],
) -> bool:
    """Paint rides on the clean stack — follow that bubble's clean layer."""
    if bubble_id is None:
        return True
    for layer in layers:
        if layer.get("kind") != "clean":
            continue
        try:
            if int(layer.get("bubble_id")) != int(bubble_id):
                continue
        except (TypeError, ValueError):
            continue
        return layer.get("enabled", True) is not False
    return True


def _merge_paint_layers(
    layers: List[Dict[str, Any]],
    paint: List[Dict[str, Any]],
    doc: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Materialize overlay strokes into per-bubble paint layers (z=150+)."""
    # Drop any prior paint layers so re-merge is idempotent.
    kept = [L for L in layers if L.get("kind") != "paint"]
    by_bubble: Dict[Optional[int], List[Dict[str, Any]]] = {}
    for stroke in paint:
        if not isinstance(stroke, dict):
            continue
        bid = stroke.get("bubble_id")
        if bid is not None:
            try:
                bid = int(bid)
            except (TypeError, ValueError):
                bid = None
        by_bubble.setdefault(bid, []).append(stroke)

    for bid, strokes in sorted(
        by_bubble.items(),
        key=lambda kv: (-1 if kv[0] is None else kv[0]),
    ):
        lid = f"pnt_{bid}" if bid is not None else "pnt_page"
        z = _PAINT_Z_BASE + (0 if bid is None else int(bid))
        bbox = _bubble_bbox(doc, bid)
        kept.append(
            {
                "id": lid,
                "kind": "paint",
                "name": (
                    f"Paint / bubble {bid}" if bid is not None else "Paint / page"
                ),
                "group": f"bubble_{bid}" if bid is not None else None,
                "bubble_id": bid,
                "enabled": _clean_enabled_for_bubble(kept, bid),
                "opacity": 1.0,
                "blend": "normal",
                "z": z,
                "source_step": "paint.v1",
                "bbox": bbox,
                "strokes": strokes,
            }
        )
    return kept


def parse_edit_json(edit_json: str | None) -> Dict[str, Any]:
    if not edit_json:
        return empty_edit()
    try:
        raw = json.loads(edit_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"corrupt edit_json: {e}") from e
    return validate_edit(raw)


def load_page_edit(
    conn: Any, page_id: int
) -> Optional[Tuple[Dict[str, Any], int, Optional[str]]]:
    """Return (edit, rev, author) or None if no row."""
    row = conn.execute(
        "SELECT rev, edit_json, author FROM page_edit WHERE page_id = ?",
        (page_id,),
    ).fetchone()
    if row is None:
        return None
    edit = parse_edit_json(row["edit_json"])
    edit["rev"] = int(row["rev"])
    author = row["author"] if "author" in row.keys() else None
    return edit, int(row["rev"]), author


def upsert_page_edit(
    conn: Any,
    page_id: int,
    edit: Dict[str, Any],
    *,
    expected_rev: int,
    author: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Write overlay. Raises ConflictError if expected_rev mismatches.

    ``author`` is stored on the row (roller seam), not inside edit_json.
    """
    validated = validate_edit(edit)
    validated["rev"] = expected_rev  # client sends current rev; we bump on write

    row = conn.execute(
        "SELECT rev FROM page_edit WHERE page_id = ?",
        (page_id,),
    ).fetchone()
    current_rev = int(row["rev"]) if row is not None else 0
    if expected_rev != current_rev:
        raise ConflictError(current_rev)

    new_rev = current_rev + 1
    validated["rev"] = new_rev
    done = count_done(validated)
    translated = count_translated(validated)
    payload = json.dumps(validated, ensure_ascii=False, separators=(",", ":"))
    # Empty string → NULL so single-user letterer stays author-less.
    author_val = author.strip() if isinstance(author, str) and author.strip() else None

    if row is None:
        conn.execute(
            """
            INSERT INTO page_edit
                (page_id, rev, edit_json, done_count, translated_count,
                 author, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (page_id, new_rev, payload, done, translated, author_val),
        )
    else:
        conn.execute(
            """
            UPDATE page_edit
            SET rev = ?, edit_json = ?, done_count = ?, translated_count = ?,
                author = ?, updated_at = datetime('now')
            WHERE page_id = ?
            """,
            (new_rev, payload, done, translated, author_val, page_id),
        )
    return validated, author_val


def delete_page_edit(conn: Any, page_id: int) -> bool:
    cur = conn.execute("DELETE FROM page_edit WHERE page_id = ?", (page_id,))
    return cur.rowcount > 0


class ConflictError(Exception):
    def __init__(self, current_rev: int):
        self.current_rev = current_rev
        super().__init__(f"edit conflict: server rev={current_rev}")

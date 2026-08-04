"""PageEdit overlay: validate, persist helpers, and seed→working merge.

The seed PageDoc is immutable. All letterer edits live in a separate overlay
stored in SQLite (`page_edit`). Client and server must produce the same
working doc from `merge_page_edit(seed, edit)`.
"""

from __future__ import annotations

import copy
import json
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
    }
)

_ALIGN_OK = frozenset({"left", "center", "right"})
_PAINT_MODES = frozenset({"fill", "restore"})


def empty_edit(*, rev: int = 0) -> Dict[str, Any]:
    return {"rev": rev, "page": {}, "bubbles": {}, "paint": []}


def count_done(edit: Dict[str, Any] | None) -> int:
    if not edit:
        return 0
    bubbles = edit.get("bubbles") or {}
    return sum(1 for be in bubbles.values() if isinstance(be, dict) and be.get("done"))


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

    return {"rev": rev, "page": page, "bubbles": bubbles, "paint": paint}


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
    - page.font is returned via the edit itself (compose takes font separately)

    Font size / frame / leading do not rewrite seed layouts here — those go
    through typeset and land as text layers in the working doc the client
    (or a typeset pass) builds.
    """
    if not edit:
        return seed_doc

    doc = copy.deepcopy(seed_doc)
    bubbles_edit = edit.get("bubbles") or {}
    if not bubbles_edit:
        return doc

    texts = list(doc.get("texts") or [])
    by_id: Dict[int, Dict[str, Any]] = {}
    for t in texts:
        try:
            by_id[int(t["bubble_id"])] = t
        except (KeyError, TypeError, ValueError):
            continue

    off_ids: set[str] = set()
    for key, be in bubbles_edit.items():
        if not isinstance(be, dict):
            continue
        try:
            bid = int(key)
        except (TypeError, ValueError):
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

    doc["texts"] = texts

    if off_ids:
        layers = list(doc.get("layers") or [])
        for layer in layers:
            if layer.get("id") in off_ids:
                layer["enabled"] = False
        doc["layers"] = layers

    return doc


def parse_edit_json(edit_json: str | None) -> Dict[str, Any]:
    if not edit_json:
        return empty_edit()
    try:
        raw = json.loads(edit_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"corrupt edit_json: {e}") from e
    return validate_edit(raw)


def load_page_edit(conn: Any, page_id: int) -> Optional[Tuple[Dict[str, Any], int]]:
    """Return (edit, rev) or None if no row."""
    row = conn.execute(
        "SELECT rev, edit_json FROM page_edit WHERE page_id = ?",
        (page_id,),
    ).fetchone()
    if row is None:
        return None
    edit = parse_edit_json(row["edit_json"])
    edit["rev"] = int(row["rev"])
    return edit, int(row["rev"])


def upsert_page_edit(
    conn: Any,
    page_id: int,
    edit: Dict[str, Any],
    *,
    expected_rev: int,
) -> Dict[str, Any]:
    """Write overlay. Raises ConflictError if expected_rev mismatches."""
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
    payload = json.dumps(validated, ensure_ascii=False, separators=(",", ":"))

    if row is None:
        conn.execute(
            """
            INSERT INTO page_edit (page_id, rev, edit_json, done_count, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (page_id, new_rev, payload, done),
        )
    else:
        conn.execute(
            """
            UPDATE page_edit
            SET rev = ?, edit_json = ?, done_count = ?, updated_at = datetime('now')
            WHERE page_id = ?
            """,
            (new_rev, payload, done, page_id),
        )
    return validated


def delete_page_edit(conn: Any, page_id: int) -> bool:
    cur = conn.execute("DELETE FROM page_edit WHERE page_id = ?", (page_id,))
    return cur.rowcount > 0


class ConflictError(Exception):
    def __init__(self, current_rev: int):
        self.current_rev = current_rev
        super().__init__(f"edit conflict: server rev={current_rev}")

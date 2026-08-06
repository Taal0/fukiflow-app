"""Chapter upload (ZIP / multi-file) with SSE progress via full vision pipeline."""

from __future__ import annotations

import base64
import io
import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image, ImageDraw, ImageFilter

from vision.document import Layer, PageDoc

from ..db import get_db
from ..settings import get_settings
from ..storage import chapter_dir
from ..vision_client import vision_pipeline

router = APIRouter(prefix="/api/chapters", tags=["upload"])

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_CROP_PAD = 4
_PAGE_DPI = 350.0


def _slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "chapter"


def _natural_key(s: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def _safe_stem(filename: str, fallback: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip("-._")
    return stem or fallback


def _crop_clean_layer(
    full_rgba: Image.Image,
    bbox: tuple[int, int, int, int],
    *,
    pad: int = _CROP_PAD,
) -> tuple[Image.Image, int, int]:
    x1, y1, x2, y2 = bbox
    w, h = full_rgba.size
    ox = max(0, int(x1) - pad)
    oy = max(0, int(y1) - pad)
    ex = min(w, int(x2) + pad)
    ey = min(h, int(y2) + pad)
    if ex <= ox or ey <= oy:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0)), ox, oy
    return full_rgba.crop((ox, oy, ex, ey)), ox, oy


def _rasterize_clean_layer(layer: Layer, size: tuple[int, int]) -> Image.Image:
    """PIL-only clean-layer RGBA (avoids vision compose / OpenCV at upload time)."""
    w, h = size
    rgba = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if layer.fill_color is None:
        return rgba
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    poly = (layer.mask.polygon if layer.mask else None) or []
    pts: list[tuple[float, float]] = []
    for p in poly:
        if p is None or len(p) < 2:
            continue
        pts.append((float(p[0]), float(p[1])))
    if len(pts) >= 3:
        draw.polygon(pts, fill=255)
    else:
        x1, y1, x2, y2 = (int(v) for v in layer.bbox)
        draw.rectangle([x1, y1, x2, y2], fill=255)
    inset = int(layer.mask.inset) if layer.mask else 0
    for _ in range(max(0, inset)):
        mask = mask.filter(ImageFilter.MinFilter(3))
    if mask.getbbox() is None:
        return rgba
    colored = Image.new("RGBA", (w, h), (*tuple(layer.fill_color)[:3], 255))
    rgba.paste(colored, (0, 0), mask)
    return rgba


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _unique_slug(conn, base: str) -> str:
    slug = base
    n = 2
    while conn.execute("SELECT 1 FROM chapter WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _to_png_bytes(raw: bytes, filename: str) -> bytes:
    """Normalize any supported image to PNG bytes."""
    img = Image.open(io.BytesIO(raw))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _collect_from_zip(data: bytes) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise HTTPException(400, "invalid zip file") from e
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            base = Path(name).name
            if base.startswith(".") or name.startswith("__MACOSX/"):
                continue
            if Path(base).suffix.lower() not in _IMAGE_EXTS:
                continue
            out.append((base, zf.read(info)))
    out.sort(key=lambda t: _natural_key(t[0]))
    return out


async def _collect_uploads(
    files: list[UploadFile],
    zip_file: UploadFile | None,
) -> list[tuple[str, bytes]]:
    if zip_file is not None and zip_file.filename:
        raw = await zip_file.read()
        if not raw:
            raise HTTPException(400, "empty zip file")
        return _collect_from_zip(raw)

    items: list[tuple[str, bytes]] = []
    for f in files:
        if not f.filename:
            continue
        if Path(f.filename).suffix.lower() not in _IMAGE_EXTS:
            continue
        data = await f.read()
        if data:
            items.append((Path(f.filename).name, data))
    items.sort(key=lambda t: _natural_key(t[0]))
    return items


def _build_page_doc(payload: dict[str, Any], *, stem: str, orig_name: str) -> PageDoc:
    bubbles = payload.get("bubbles") or []
    clean_layers_raw = payload.get("clean_layers") or []
    layers = [Layer.model_validate(L) for L in clean_layers_raw]
    layers = sorted(layers, key=lambda L: (L.z, L.id))
    editor_ids = [
        int(b["id"])
        for b in bubbles
        if isinstance(b, dict) and b.get("clean_status") == "editor" and "id" in b
    ]
    return PageDoc.model_validate(
        {
            "schema_version": 1,
            "page_id": payload.get("page_id") or stem,
            "source": {
                "file": orig_name,
                "width": int(payload.get("width") or 0),
                "height": int(payload.get("height") or 0),
                "dpi": _PAGE_DPI,
                "color_mode": "rgb",
            },
            "bubbles": bubbles,
            "texts": [],
            "layers": [L.model_dump() for L in layers],
            "flags": {
                "clean": payload.get("clean") or "ok",
                "typeset": "skipped",
                "editor_bubble_ids": editor_ids,
                "typeset_editor_bubble_ids": [],
            },
        }
    )


def _write_layer_crops(
    conn,
    *,
    page_id: int,
    out_dir: Path,
    stem: str,
    slug: str,
    doc: PageDoc,
) -> int:
    crop_dir = out_dir / f"{stem}-layers-crop"
    crop_dir.mkdir(exist_ok=True)
    w = int(doc.source.width)
    h = int(doc.source.height)
    count = 0
    for layer in doc.layers:
        if layer.kind != "clean":
            continue
        full = _rasterize_clean_layer(layer, (w, h))
        if full.getbbox() is None:
            continue
        cropped, ox, oy = _crop_clean_layer(full, tuple(layer.bbox))
        dest = crop_dir / f"{layer.id}.png"
        cropped.save(dest)
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
                f"{slug}/{stem}/{stem}-layers-crop/{layer.id}.png",
                ox,
                oy,
                cropped.size[0],
                cropped.size[1],
            ),
        )
        count += 1
    return count


def _persist_pipeline_page(
    conn,
    *,
    page_id: int,
    slug: str,
    stem: str,
    page_dir: Path,
    orig_png: bytes,
    payload: dict[str, Any],
) -> None:
    if not payload.get("cleaned_image"):
        raise RuntimeError("pipeline response missing cleaned_image")

    orig_name = f"{stem}-orig.png"
    (page_dir / orig_name).write_bytes(orig_png)
    cleaned = base64.b64decode(payload["cleaned_image"])
    (page_dir / f"{stem}.png").write_bytes(cleaned)

    if payload.get("bubbles_overlay"):
        (page_dir / f"{stem}-detect.png").write_bytes(
            base64.b64decode(payload["bubbles_overlay"])
        )

    doc = _build_page_doc(payload, stem=stem, orig_name=orig_name)
    (page_dir / f"{stem}-doc.json").write_text(
        doc.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    editor_ids = set(doc.flags.editor_bubble_ids or [])
    editor_ids.update(doc.flags.typeset_editor_bubble_ids or [])
    conn.execute(
        """
        UPDATE page SET
            width=?, height=?, dpi=?,
            clean_status=?, typeset_status=?,
            bubble_count=?, editor_bubble_count=?,
            processing_status='ready', error_message=NULL
        WHERE id=?
        """,
        (
            doc.source.width,
            doc.source.height,
            float(doc.source.dpi or _PAGE_DPI),
            doc.flags.clean or "ok",
            doc.flags.typeset or "skipped",
            len(doc.bubbles),
            len(editor_ids),
            page_id,
        ),
    )
    conn.execute("DELETE FROM page_asset WHERE page_id = ?", (page_id,))
    conn.execute("DELETE FROM layer_asset WHERE page_id = ?", (page_id,))

    rel_base = f"{slug}/{stem}"
    assets = [
        ("original", f"{rel_base}/{orig_name}"),
        ("cleaned", f"{rel_base}/{stem}.png"),
    ]
    detect = page_dir / f"{stem}-detect.png"
    if detect.is_file():
        assets.append(("detect", f"{rel_base}/{stem}-detect.png"))
    for kind, rel in assets:
        conn.execute(
            "INSERT INTO page_asset (page_id, kind, rel_path) VALUES (?, ?, ?)",
            (page_id, kind, rel),
        )

    _write_layer_crops(
        conn, page_id=page_id, out_dir=page_dir, stem=stem, slug=slug, doc=doc
    )


@router.post("/upload")
async def upload_chapter(
    title: str | None = Form(None),
    slug: str | None = Form(None),
    zip_file: UploadFile | None = File(None),
    files: list[UploadFile] = File(default=[]),
):
    """Upload a chapter (ZIP or image files) and run full vision pipeline.

    Returns ``text/event-stream`` with ``page_start`` / ``page_done`` /
    ``page_error`` / ``complete`` events.
    """
    images = await _collect_uploads(files, zip_file)
    if not images:
        raise HTTPException(
            400,
            "provide zip_file or image files (png/jpg/jpeg/webp)",
        )

    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    base_slug = _slugify(slug or title or Path(images[0][0]).stem or "chapter")
    chapter_title = (title or base_slug).strip() or base_slug

    with get_db() as conn:
        final_slug = _unique_slug(conn, base_slug)
        chapter_path = chapter_dir(final_slug, "upload")
        if chapter_path.exists():
            shutil.rmtree(chapter_path)
        chapter_path.mkdir(parents=True)

        cur = conn.execute(
            """
            INSERT INTO chapter (slug, title, source_dir, source_type)
            VALUES (?, ?, ?, 'upload')
            """,
            (final_slug, chapter_title, final_slug),
        )
        chapter_id = int(cur.lastrowid)

        page_rows: list[tuple[int, int, str, bytes]] = []
        used_stems: set[str] = set()
        for i, (filename, raw) in enumerate(images):
            stem = _safe_stem(filename, f"page_{i + 1}")
            if stem in used_stems:
                stem = f"{stem}_{i + 1}"
            used_stems.add(stem)
            try:
                png = _to_png_bytes(raw, filename)
            except Exception as e:
                raise HTTPException(400, f"invalid image {filename}: {e}") from e
            with Image.open(io.BytesIO(png)) as im:
                w, h = im.size
            page_no = i + 1
            cur = conn.execute(
                """
                INSERT INTO page (
                    chapter_id, page_no, stem, width, height, dpi,
                    clean_status, typeset_status, bubble_count, editor_bubble_count,
                    processing_status
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 'skipped', 0, 0, 'pending')
                """,
                (chapter_id, page_no, stem, w, h, _PAGE_DPI),
            )
            page_rows.append((int(cur.lastrowid), page_no, stem, png))

    async def event_stream() -> AsyncIterator[str]:
        errors: list[dict[str, Any]] = []
        total = len(page_rows)
        chapter_path = chapter_dir(final_slug, "upload")

        for page_id, page_no, stem, png in page_rows:
            yield _sse(
                "page_start",
                {"page_no": page_no, "total": total, "stem": stem, "page_id": page_id},
            )
            page_dir = chapter_path / stem
            page_dir.mkdir(parents=True, exist_ok=True)

            with get_db() as conn:
                conn.execute(
                    "UPDATE page SET processing_status='processing', error_message=NULL WHERE id=?",
                    (page_id,),
                )

            try:
                payload = await vision_pipeline(
                    image_bytes=png,
                    filename=f"{stem}.png",
                    page_id=stem,
                    include_layers=True,
                )
                with get_db() as conn:
                    _persist_pipeline_page(
                        conn,
                        page_id=page_id,
                        slug=final_slug,
                        stem=stem,
                        page_dir=page_dir,
                        orig_png=png,
                        payload=payload,
                    )
                yield _sse(
                    "page_done",
                    {
                        "page_no": page_no,
                        "total": total,
                        "stem": stem,
                        "page_id": page_id,
                        "status": "ready",
                    },
                )
            except Exception as e:
                msg = str(getattr(e, "detail", None) or e)
                errors.append({"page_no": page_no, "stem": stem, "error": msg})
                try:
                    (page_dir / f"{stem}-orig.png").write_bytes(png)
                except OSError:
                    pass
                with get_db() as conn:
                    conn.execute(
                        """
                        UPDATE page SET processing_status='error', error_message=?
                        WHERE id=?
                        """,
                        (msg[:2000], page_id),
                    )
                    conn.execute(
                        "DELETE FROM page_asset WHERE page_id = ?", (page_id,)
                    )
                    conn.execute(
                        """
                        INSERT INTO page_asset (page_id, kind, rel_path)
                        VALUES (?, 'original', ?)
                        """,
                        (page_id, f"{final_slug}/{stem}/{stem}-orig.png"),
                    )
                yield _sse(
                    "page_error",
                    {
                        "page_no": page_no,
                        "total": total,
                        "stem": stem,
                        "page_id": page_id,
                        "error": msg,
                    },
                )

        yield _sse(
            "complete",
            {
                "slug": final_slug,
                "title": chapter_title,
                "pages": total,
                "errors": errors,
            },
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

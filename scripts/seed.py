#!/usr/bin/env python3
"""Import a batch_clean output folder into the app seed catalog.

Usage:
  python scripts/seed.py "/path/to/test manga-translated v28" [--slug test-manga] [--title "Test Manga"]

Idempotent: re-running updates the same chapter.slug.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from PIL import Image

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.db import connect, init_db  # noqa: E402
from app.settings import get_settings  # noqa: E402

# PageDoc comes from the editable `fukiflow-vision` install (vision package only).
from vision.document import PageDoc  # noqa: E402

CROP_PAD = 4


def _slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "chapter"


def _page_no(stem: str) -> int:
    m = re.search(r"(\d+)$", stem)
    return int(m.group(1)) if m else 0


def _find_page_dirs(batch_dir: Path) -> list[Path]:
    dirs = []
    for child in sorted(batch_dir.iterdir()):
        if not child.is_dir():
            continue
        docs = list(child.glob("*-doc.json"))
        if docs:
            dirs.append(child)
    return dirs


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _crop_clean_layer(
    full_rgba: Image.Image,
    bbox: tuple[int, int, int, int],
    *,
    pad: int = CROP_PAD,
) -> tuple[Image.Image, int, int]:
    x1, y1, x2, y2 = bbox
    w, h = full_rgba.size
    ox = max(0, int(x1) - pad)
    oy = max(0, int(y1) - pad)
    ex = min(w, int(x2) + pad)
    ey = min(h, int(y2) + pad)
    if ex <= ox or ey <= oy:
        # Degenerate bbox → 1x1 transparent
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0)), ox, oy
    return full_rgba.crop((ox, oy, ex, ey)), ox, oy


def _upsert_chapter(conn, slug: str, title: str, source_dir: str) -> int:
    row = conn.execute("SELECT id FROM chapter WHERE slug = ?", (slug,)).fetchone()
    if row:
        conn.execute(
            "UPDATE chapter SET title = ?, source_dir = ? WHERE id = ?",
            (title, source_dir, row["id"]),
        )
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO chapter (slug, title, source_dir) VALUES (?, ?, ?)",
        (slug, title, source_dir),
    )
    return int(cur.lastrowid)


def _replace_page(conn, chapter_id: int, stem: str, doc: PageDoc, page_no: int) -> int:
    existing = conn.execute(
        "SELECT id FROM page WHERE chapter_id = ? AND stem = ?",
        (chapter_id, stem),
    ).fetchone()
    editor_ids = set(doc.flags.editor_bubble_ids or [])
    editor_ids.update(doc.flags.typeset_editor_bubble_ids or [])
    fields = (
        page_no,
        doc.source.width,
        doc.source.height,
        float(doc.source.dpi or 350),
        doc.flags.clean or "ok",
        doc.flags.typeset or "ok",
        len(doc.bubbles),
        len(editor_ids),
    )
    if existing:
        page_id = int(existing["id"])
        conn.execute(
            """
            UPDATE page SET page_no=?, width=?, height=?, dpi=?,
                clean_status=?, typeset_status=?, bubble_count=?,
                editor_bubble_count=?
            WHERE id=?
            """,
            (*fields, page_id),
        )
        conn.execute("DELETE FROM page_asset WHERE page_id = ?", (page_id,))
        conn.execute("DELETE FROM layer_asset WHERE page_id = ?", (page_id,))
        return page_id
    cur = conn.execute(
        """
        INSERT INTO page (
            chapter_id, page_no, stem, width, height, dpi,
            clean_status, typeset_status, bubble_count, editor_bubble_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (chapter_id, *fields[:1], stem, *fields[1:]),
    )
    return int(cur.lastrowid)


def _add_asset(conn, page_id: int, kind: str, rel_path: str) -> None:
    conn.execute(
        "INSERT INTO page_asset (page_id, kind, rel_path) VALUES (?, ?, ?)",
        (page_id, kind, rel_path),
    )


def seed_batch(
    batch_dir: Path,
    *,
    slug: str,
    title: str,
    seed_dir: Path,
) -> dict:
    page_dirs = _find_page_dirs(batch_dir)
    if not page_dirs:
        raise SystemExit(f"no page folders with *-doc.json under {batch_dir}")

    chapter_out = seed_dir / slug
    chapter_out.mkdir(parents=True, exist_ok=True)

    conn = connect()
    init_db(conn)
    chapter_id = _upsert_chapter(conn, slug, title, batch_dir.name)

    imported = 0
    layer_count = 0
    skipped: list[str] = []

    for page_dir in page_dirs:
        docs = sorted(page_dir.glob("*-doc.json"))
        if not docs:
            skipped.append(str(page_dir))
            continue
        doc_src = docs[0]
        stem = doc_src.name[: -len("-doc.json")]
        try:
            doc = PageDoc.model_validate(
                json.loads(doc_src.read_text(encoding="utf-8"))
            )
        except Exception as e:
            skipped.append(f"{page_dir.name}: bad doc ({e})")
            continue

        out_dir = chapter_out / stem
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)

        # Copy PageDoc + key rasters
        _copy(doc_src, out_dir / f"{stem}-doc.json")

        asset_map = {
            "original": [
                page_dir / f"{stem}-orig.png",
                page_dir / f"{stem}-orig.jpg",
                page_dir / f"{stem}-orig.jpeg",
                page_dir / f"{stem}-orig.webp",
            ],
            "cleaned": [page_dir / f"{stem}.png"],
            "compose": [page_dir / f"{stem}-compose.png"],
            "detect": [page_dir / f"{stem}-detect.png"],
        }

        page_id = _replace_page(conn, chapter_id, stem, doc, _page_no(stem))
        rel_base = f"{slug}/{stem}"

        for kind, candidates in asset_map.items():
            src = next((p for p in candidates if p.is_file()), None)
            if src is None:
                continue
            dst_name = src.name
            _copy(src, out_dir / dst_name)
            _add_asset(conn, page_id, kind, f"{rel_base}/{dst_name}")

        # Also keep typeset.json if present (handy for debugging).
        typeset_json = page_dir / f"{stem}-typeset.json"
        if typeset_json.is_file():
            _copy(typeset_json, out_dir / typeset_json.name)

        # Crop clean layers
        layers_src = page_dir / f"{stem}-layers"
        crop_dir = out_dir / f"{stem}-layers-crop"
        crop_dir.mkdir(exist_ok=True)

        for layer in doc.layers:
            if layer.kind != "clean":
                continue
            src_png = layers_src / f"{layer.id}.png"
            if not src_png.is_file():
                skipped.append(f"{stem}: missing layer {layer.id}.png")
                continue
            full = Image.open(src_png).convert("RGBA")
            cropped, ox, oy = _crop_clean_layer(full, tuple(layer.bbox))
            dst = crop_dir / f"{layer.id}.png"
            cropped.save(dst)
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
                    f"{rel_base}/{stem}-layers-crop/{layer.id}.png",
                    ox,
                    oy,
                    cropped.size[0],
                    cropped.size[1],
                ),
            )
            layer_count += 1

        imported += 1

    conn.commit()
    conn.close()

    summary = {
        "chapter": slug,
        "title": title,
        "pages": imported,
        "clean_layers": layer_count,
        "skipped": skipped,
        "seed_dir": str(seed_dir),
    }
    return summary


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Seed fukiflow-app from batch_clean output")
    ap.add_argument("batch_dir", type=Path, help="batch_clean output directory")
    ap.add_argument("--slug", default=None, help="chapter slug (default: from folder name)")
    ap.add_argument("--title", default=None, help="chapter title")
    ap.add_argument(
        "--seed-dir",
        type=Path,
        default=None,
        help="override SEED_DIR (default from settings)",
    )
    args = ap.parse_args(argv[1:])

    batch_dir = args.batch_dir.expanduser().resolve()
    if not batch_dir.is_dir():
        print(f"error: not a directory: {batch_dir}", file=sys.stderr)
        return 1

    slug = args.slug or _slugify(batch_dir.name.replace("translated", "").strip())
    # Prefer a stable demo slug when importing the test manga batch.
    if not args.slug and "test" in batch_dir.name.lower() and "manga" in batch_dir.name.lower():
        slug = "test-manga"
    title = args.title or batch_dir.name
    seed_dir = (args.seed_dir or get_settings().seed_dir).expanduser().resolve()
    seed_dir.mkdir(parents=True, exist_ok=True)

    summary = seed_batch(batch_dir, slug=slug, title=title, seed_dir=seed_dir)
    print(
        f"seeded chapter={summary['chapter']!r} pages={summary['pages']} "
        f"clean_layers={summary['clean_layers']} → {summary['seed_dir']}"
    )
    if summary["skipped"]:
        print(f"skipped ({len(summary['skipped'])}):")
        for s in summary["skipped"][:20]:
            print(f"  - {s}")
        if len(summary["skipped"]) > 20:
            print(f"  … +{len(summary['skipped']) - 20} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

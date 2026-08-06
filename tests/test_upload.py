"""Upload helpers (no vision service required)."""

from __future__ import annotations

import io
import zipfile

from app.routers.upload import _collect_from_zip, _natural_key, _slugify


def test_slugify():
    assert _slugify("My Manga!!") == "my-manga"
    assert _slugify("  ") == "chapter"


def test_natural_sort_order():
    names = ["page_10.png", "page_2.png", "page_1.png"]
    assert sorted(names, key=_natural_key) == ["page_1.png", "page_2.png", "page_10.png"]


def test_collect_from_zip_filters_and_sorts():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("__MACOSX/._x.png", b"x")
        zf.writestr("a/page_10.png", b"\x89PNG\r\n\x1a\n")
        zf.writestr("a/page_2.png", b"\x89PNG\r\n\x1a\n")
        zf.writestr("readme.txt", b"nope")
    names = [n for n, _ in _collect_from_zip(buf.getvalue())]
    assert names == ["page_2.png", "page_10.png"]

"""PageEdit validate / merge / order parity tests."""

from __future__ import annotations

import pytest

from app.edits import (
    count_translated,
    empty_edit,
    merge_page_edit,
    ordered_items,
    validate_edit,
)


def _seed(*bubbles: dict) -> dict:
    return {
        "page_id": "p1",
        "source": {"width": 1000, "height": 1400},
        "bubbles": list(bubbles),
        "texts": [],
        "layers": [],
    }


def test_empty_edit_has_regions_and_order():
    e = empty_edit()
    assert e["regions"] == []
    assert e["order"] == []


def test_validate_legacy_edit_without_regions():
    out = validate_edit({"rev": 2, "page": {}, "bubbles": {"3": {"text": "hi"}}, "paint": []})
    assert out["regions"] == []
    assert out["order"] == []
    assert out["bubbles"]["3"]["text"] == "hi"


def test_validate_region_ok():
    out = validate_edit(
        {
            "rev": 1,
            "bubbles": {},
            "paint": [],
            "regions": [
                {
                    "id": "r-8f2a",
                    "bbox": [10, 20, 100, 80],
                    "text": "GÜMM!",
                    "status": "unclassified",
                    "confidence": None,
                }
            ],
            "order": ["r-8f2a"],
        }
    )
    assert len(out["regions"]) == 1
    assert out["regions"][0]["id"] == "r-8f2a"
    assert out["order"] == ["r-8f2a"]


def test_validate_region_bad_bbox():
    with pytest.raises(ValueError, match="bbox"):
        validate_edit(
            {
                "rev": 0,
                "bubbles": {},
                "regions": [
                    {
                        "id": "r-ab",
                        "bbox": [100, 20, 10, 80],
                        "text": "",
                        "status": "unclassified",
                    }
                ],
            }
        )


def test_validate_region_unknown_field():
    with pytest.raises(ValueError, match="unknown fields"):
        validate_edit(
            {
                "rev": 0,
                "bubbles": {},
                "regions": [
                    {
                        "id": "r-ab",
                        "bbox": [1, 2, 3, 4],
                        "text": "",
                        "status": "unclassified",
                        "extra": True,
                    }
                ],
            }
        )


def test_validate_order_drops_orphan_region_tokens():
    out = validate_edit(
        {
            "rev": 0,
            "bubbles": {},
            "regions": [
                {
                    "id": "r-keep",
                    "bbox": [1, 2, 30, 40],
                    "text": "",
                    "status": "unclassified",
                }
            ],
            "order": ["b:3", "r-gone", "r-keep", "b:3"],
        }
    )
    assert out["order"] == ["b:3", "r-keep"]


def test_validate_order_rejects_bad_token():
    with pytest.raises(ValueError, match="order"):
        validate_edit({"rev": 0, "bubbles": {}, "order": ["nope"]})


def test_ordered_items_and_merge_renumber():
    seed = _seed(
        {"id": 3, "bbox": [0, 0, 10, 10], "polygon": [], "reading_order": 1},
        {"id": 5, "bbox": [0, 0, 10, 10], "polygon": [], "reading_order": 2},
        {"id": 4, "bbox": [0, 0, 10, 10], "polygon": [], "reading_order": 3},
    )
    edit = validate_edit(
        {
            "rev": 1,
            "bubbles": {"3": {"text": "a"}},
            "regions": [
                {
                    "id": "r-8f2a",
                    "bbox": [120, 340, 380, 420],
                    "text": "GÜMM!",
                    "status": "unclassified",
                    "confidence": None,
                }
            ],
            "order": ["b:3", "r-8f2a", "b:5", "b:4"],
        }
    )
    assert ordered_items(seed, edit) == [
        ("bubble", "3"),
        ("region", "r-8f2a"),
        ("bubble", "5"),
        ("bubble", "4"),
    ]
    merged = merge_page_edit(seed, edit)
    by_id = {int(b["id"]): b for b in merged["bubbles"]}
    assert by_id[3]["reading_order"] == 1
    assert by_id[5]["reading_order"] == 3
    assert by_id[4]["reading_order"] == 4
    assert by_id[3]["order_source"] == "manual"
    # Regions never injected into PageDoc
    assert "regions" not in merged
    assert all(b.get("id") != "r-8f2a" for b in merged["bubbles"])


def test_merge_legacy_edit_preserves_reading_order():
    seed = _seed(
        {"id": 1, "bbox": [0, 0, 10, 10], "polygon": [], "reading_order": 7},
    )
    edit = validate_edit({"rev": 0, "bubbles": {"1": {"text": "x"}}, "paint": []})
    merged = merge_page_edit(seed, edit)
    assert merged["bubbles"][0]["reading_order"] == 7
    assert merged["bubbles"][0].get("order_source") != "manual"


def test_count_translated():
    edit = validate_edit(
        {
            "rev": 0,
            "bubbles": {
                "1": {"text": "  hello "},
                "2": {"text": "   "},
                "3": {"done": True},
            },
            "regions": [
                {
                    "id": "r-aa",
                    "bbox": [1, 2, 3, 4],
                    "text": "sfx",
                    "status": "unclassified",
                },
                {
                    "id": "r-bb",
                    "bbox": [1, 2, 3, 4],
                    "text": "",
                    "status": "unclassified",
                },
            ],
        }
    )
    assert count_translated(edit) == 2

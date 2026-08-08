"""PageEdit validate / merge / order parity tests."""

from __future__ import annotations

import pytest

from app.edits import (
    count_done,
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
    assert e["removed_bubbles"] == []


def test_validate_legacy_edit_without_regions():
    out = validate_edit({"rev": 2, "page": {}, "bubbles": {"3": {"text": "hi"}}, "paint": []})
    assert out["regions"] == []
    assert out["order"] == []
    assert out["removed_bubbles"] == []
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
    assert out["regions"][0]["kind"] == "bubble"  # legacy default
    assert out["order"] == ["r-8f2a"]


def test_validate_region_kind_sfx():
    out = validate_edit(
        {
            "rev": 0,
            "bubbles": {},
            "regions": [
                {
                    "id": "r-sfx01",
                    "bbox": [10, 20, 100, 80],
                    "text": "ごくん",
                    "kind": "sfx",
                    "status": "confirmed",
                    "confidence": None,
                }
            ],
            "order": ["r-sfx01"],
        }
    )
    assert out["regions"][0]["kind"] == "sfx"
    assert out["regions"][0]["status"] == "confirmed"


def test_validate_region_bad_kind():
    with pytest.raises(ValueError, match="kind"):
        validate_edit(
            {
                "rev": 0,
                "bubbles": {},
                "regions": [
                    {
                        "id": "r-ab",
                        "bbox": [1, 2, 30, 40],
                        "text": "",
                        "kind": "narration",
                        "status": "unclassified",
                    }
                ],
            }
        )


def test_validate_region_confirmed_with_bubble_id():
    out = validate_edit(
        {
            "rev": 1,
            "bubbles": {"7": {"text": "hi"}},
            "paint": [],
            "regions": [
                {
                    "id": "r-8f2a",
                    "bbox": [10, 20, 100, 80],
                    "text": "GÜMM!",
                    "status": "confirmed",
                    "confidence": 0.91,
                    "bubble_id": 7,
                }
            ],
            "order": ["b:7"],
        }
    )
    assert out["regions"][0]["status"] == "confirmed"
    assert out["regions"][0]["bubble_id"] == 7
    assert out["regions"][0]["confidence"] == 0.91


def test_validate_region_verifying_status():
    out = validate_edit(
        {
            "rev": 0,
            "bubbles": {},
            "regions": [
                {
                    "id": "r-ab12",
                    "bbox": [1, 2, 30, 40],
                    "text": "x",
                    "status": "verifying",
                    "confidence": None,
                }
            ],
        }
    )
    assert out["regions"][0]["status"] == "verifying"


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


def test_ordered_items_keeps_confirmed_sfx_without_bubble():
    seed = _seed(
        {"id": 1, "bbox": [0, 0, 10, 10], "polygon": [], "reading_order": 1},
    )
    edit = validate_edit(
        {
            "rev": 1,
            "bubbles": {},
            "regions": [
                {
                    "id": "r-sfx01",
                    "bbox": [10, 20, 100, 80],
                    "text": "ごくん",
                    "kind": "sfx",
                    "status": "confirmed",
                    "confidence": None,
                },
                {
                    "id": "r-prom1",
                    "bbox": [10, 20, 100, 80],
                    "text": "hi",
                    "kind": "bubble",
                    "status": "confirmed",
                    "confidence": 0.9,
                    "bubble_id": 1,
                },
            ],
            "order": ["b:1", "r-sfx01", "r-prom1"],
        }
    )
    # Promoted bubble region dropped; sfx stays as region.
    assert ordered_items(seed, edit) == [
        ("bubble", "1"),
        ("region", "r-sfx01"),
    ]


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


def test_validate_removed_bubbles_ok_and_dedupe():
    out = validate_edit(
        {
            "rev": 0,
            "bubbles": {},
            "removed_bubbles": [3, 1, 3, 2],
            "order": ["b:1", "b:5", "b:3"],
        }
    )
    assert out["removed_bubbles"] == [3, 1, 2]
    assert out["order"] == ["b:5"]


def test_validate_removed_bubbles_rejects_bad():
    with pytest.raises(ValueError, match="removed_bubbles"):
        validate_edit({"rev": 0, "bubbles": {}, "removed_bubbles": [-1]})
    with pytest.raises(ValueError, match="removed_bubbles"):
        validate_edit({"rev": 0, "bubbles": {}, "removed_bubbles": ["1"]})
    with pytest.raises(ValueError, match="removed_bubbles"):
        validate_edit({"rev": 0, "bubbles": {}, "removed_bubbles": True})


def test_ordered_items_skips_removed():
    seed = _seed(
        {"id": 1, "bbox": [0, 0, 10, 10], "polygon": [], "reading_order": 1},
        {"id": 2, "bbox": [0, 0, 10, 10], "polygon": [], "reading_order": 2},
        {"id": 3, "bbox": [0, 0, 10, 10], "polygon": [], "reading_order": 3},
    )
    edit = validate_edit(
        {
            "rev": 0,
            "bubbles": {},
            "removed_bubbles": [2],
            "order": ["b:1", "b:2", "b:3"],
        }
    )
    assert ordered_items(seed, edit) == [("bubble", "1"), ("bubble", "3")]


def test_merge_page_edit_removes_bubble_texts_layers():
    seed = {
        "page_id": "p1",
        "source": {"width": 100, "height": 100},
        "bubbles": [
            {"id": 1, "bbox": [0, 0, 10, 10], "polygon": [], "reading_order": 1},
            {"id": 2, "bbox": [0, 0, 10, 10], "polygon": [], "reading_order": 2},
        ],
        "texts": [
            {"bubble_id": 1, "text": "a"},
            {"bubble_id": 2, "text": "b"},
        ],
        "layers": [
            {"id": "cln_1", "kind": "clean", "bubble_id": 1, "enabled": True},
            {"id": "txt_1", "kind": "text", "bubble_id": 1, "enabled": True},
            {"id": "cln_2", "kind": "clean", "bubble_id": 2, "enabled": True},
            {"id": "txt_2", "kind": "text", "bubble_id": 2, "enabled": True},
            {"id": "bg", "kind": "source", "bubble_id": None, "enabled": True},
        ],
    }
    edit = validate_edit(
        {
            "rev": 1,
            "bubbles": {"2": {"text": "gone"}},
            "paint": [
                {
                    "id": "s1",
                    "bubble_id": 2,
                    "mode": "fill",
                    "color": [0, 0, 0],
                    "radius_px": 4,
                    "points": [[1, 1]],
                }
            ],
            "removed_bubbles": [2],
            "order": ["b:1", "b:2"],
        }
    )
    merged = merge_page_edit(seed, edit)
    assert [int(b["id"]) for b in merged["bubbles"]] == [1]
    assert [int(t["bubble_id"]) for t in merged["texts"]] == [1]
    layer_ids = [L["id"] for L in merged["layers"]]
    assert "cln_2" not in layer_ids
    assert "txt_2" not in layer_ids
    assert "pnt_2" not in layer_ids
    assert "cln_1" in layer_ids
    assert "bg" in layer_ids


def test_count_done_and_translated_skip_removed():
    edit = validate_edit(
        {
            "rev": 0,
            "bubbles": {
                "1": {"text": "hi", "done": True},
                "2": {"text": "bye", "done": True},
            },
            "removed_bubbles": [2],
            "regions": [
                {
                    "id": "r-aa",
                    "bbox": [1, 2, 3, 4],
                    "text": "sfx",
                    "status": "unclassified",
                }
            ],
        }
    )
    assert count_done(edit) == 1
    assert count_translated(edit) == 2  # bubble 1 + region

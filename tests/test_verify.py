"""Unit tests for region YOLO verify helpers."""

from __future__ import annotations

from app.routers.verify import iou, padded_crop_box


def test_iou_identical():
    box = [10.0, 20.0, 110.0, 120.0]
    assert abs(iou(box, box) - 1.0) < 1e-6


def test_iou_no_overlap():
    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0


def test_iou_partial():
    a = [0.0, 0.0, 10.0, 10.0]
    b = [5.0, 5.0, 15.0, 15.0]
    # inter=25, union=100+100-25=175
    assert abs(iou(a, b) - 25.0 / 175.0) < 1e-6


def test_padded_crop_interior():
    x0, y0, x1, y1 = padded_crop_box(
        [100, 200, 200, 300],
        1000,
        1400,
        pad_ratio=0.15,
        min_pad_px=80,
    )
    # bbox 100x100 → pad max(15, 80)=80
    assert (x0, y0, x1, y1) == (20, 120, 280, 380)


def test_padded_crop_clamps_to_edge():
    x0, y0, x1, y1 = padded_crop_box(
        [10, 10, 50, 50],
        500,
        500,
        pad_ratio=0.15,
        min_pad_px=80,
    )
    assert x0 == 0
    assert y0 == 0
    assert x1 == 130
    assert y1 == 130

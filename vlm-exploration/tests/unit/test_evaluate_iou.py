# tests/unit/test_evaluate_iou.py
"""Unit tests for pure math functions in src/exploration/evaluate_iou.py."""

import pytest

from src.exploration.evaluate_iou import (
    bbox_area,
    bbox_center,
    center_error,
    intersection_area,
    iou,
    to_bbox,
)


# -------------------------------------------------------------------
# to_bbox
# -------------------------------------------------------------------
class TestToBbox:
    def test_valid(self):
        b = to_bbox([10.0, 20.0, 30.0, 40.0])
        assert b == (10.0, 20.0, 30.0, 40.0)

    def test_wrong_length_raises(self):
        with pytest.raises(ValueError):
            to_bbox([10.0, 20.0, 30.0])

    def test_inverted_x_raises(self):
        with pytest.raises(ValueError):
            to_bbox([30.0, 20.0, 10.0, 40.0])

    def test_inverted_y_raises(self):
        with pytest.raises(ValueError):
            to_bbox([10.0, 40.0, 30.0, 20.0])

    def test_coerces_ints_to_float(self):
        b = to_bbox([0, 0, 100, 200])
        assert b == (0.0, 0.0, 100.0, 200.0)


# -------------------------------------------------------------------
# bbox_area
# -------------------------------------------------------------------
class TestBboxArea:
    def test_area(self):
        assert bbox_area((0.0, 0.0, 10.0, 5.0)) == pytest.approx(50.0)

    def test_unit_square(self):
        assert bbox_area((0.0, 0.0, 1.0, 1.0)) == pytest.approx(1.0)


# -------------------------------------------------------------------
# intersection_area
# -------------------------------------------------------------------
class TestIntersectionArea:
    def test_full_overlap(self):
        b = (0.0, 0.0, 10.0, 10.0)
        assert intersection_area(b, b) == pytest.approx(100.0)

    def test_no_overlap(self):
        a = (0.0, 0.0, 5.0, 5.0)
        b = (6.0, 6.0, 10.0, 10.0)
        assert intersection_area(a, b) == pytest.approx(0.0)

    def test_touching_edge_no_overlap(self):
        a = (0.0, 0.0, 5.0, 5.0)
        b = (5.0, 0.0, 10.0, 5.0)
        assert intersection_area(a, b) == pytest.approx(0.0)

    def test_partial_overlap(self):
        a = (0.0, 0.0, 4.0, 4.0)
        b = (2.0, 2.0, 6.0, 6.0)
        # overlap = [2,2,4,4] = 2*2 = 4
        assert intersection_area(a, b) == pytest.approx(4.0)


# -------------------------------------------------------------------
# iou
# -------------------------------------------------------------------
class TestIou:
    def test_identical_boxes(self):
        b = (0.0, 0.0, 10.0, 10.0)
        assert iou(b, b) == pytest.approx(1.0)

    def test_no_overlap(self):
        a = (0.0, 0.0, 5.0, 5.0)
        b = (6.0, 6.0, 10.0, 10.0)
        assert iou(a, b) == pytest.approx(0.0)

    def test_partial_overlap_value(self):
        # a = [0,0,4,4] area=16; b = [2,2,6,6] area=16; inter=4; union=28
        a = (0.0, 0.0, 4.0, 4.0)
        b = (2.0, 2.0, 6.0, 6.0)
        expected = 4.0 / (16.0 + 16.0 - 4.0)
        assert iou(a, b) == pytest.approx(expected)

    def test_one_inside_other(self):
        outer = (0.0, 0.0, 10.0, 10.0)
        inner = (2.0, 2.0, 5.0, 5.0)
        # inter = inner area = 9; union = 100
        assert iou(outer, inner) == pytest.approx(9.0 / 100.0)

    def test_result_range(self):
        a = (0.0, 0.0, 3.0, 3.0)
        b = (1.0, 1.0, 4.0, 4.0)
        result = iou(a, b)
        assert 0.0 <= result <= 1.0


# -------------------------------------------------------------------
# bbox_center / center_error
# -------------------------------------------------------------------
class TestCenterError:
    def test_same_center(self):
        b = (0.0, 0.0, 10.0, 10.0)
        assert center_error(b, b) == pytest.approx(0.0)

    def test_known_offset(self):
        a = (0.0, 0.0, 10.0, 10.0)   # center (5, 5)
        b = (4.0, 4.0, 14.0, 14.0)   # center (9, 9) → offset (4, 4)
        assert center_error(a, b) == pytest.approx((4**2 + 4**2) ** 0.5)

    def test_horizontal_offset_only(self):
        a = (0.0, 0.0, 2.0, 2.0)     # center (1, 1)
        b = (4.0, 0.0, 6.0, 2.0)     # center (5, 1) → offset (4, 0)
        assert center_error(a, b) == pytest.approx(4.0)

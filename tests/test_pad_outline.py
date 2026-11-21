"""Pad outlines in the pad frame, and their placement (synthetic only)."""

import math

import pytest
from shapely.geometry import LineString, Point, box

from pdnlab_dc.pad_outline import (CORNERS, chamfer_outline, circle_outline, contour_outline,
                                   oval_outline, place, rect_outline, roundrect_outline)

TOL = 1e-3   # 1 µm in millimetres
EPS = 1e-9


def core(width, height, radius):
    """Points at distance ``radius`` from this make up the exact rounded shape's boundary."""
    cx, cy = width / 2 - radius, height / 2 - radius
    if cx > 0 and cy > 0:
        return box(-cx, -cy, cx, cy).exterior
    return LineString([(-cx, -cy), (cx, cy)]) if (cx or cy) else Point(0, 0)


def check_rounded(polygon, width, height, radius, tol=TOL):
    ref = core(width, height, radius)
    ring = list(polygon.exterior.coords)
    for p in ring:
        assert ref.distance(Point(p)) == pytest.approx(radius, abs=EPS)
    for p, q in zip(ring, ring[1:]):
        mid = ref.distance(Point((p[0] + q[0]) / 2, (p[1] + q[1]) / 2))
        assert radius - tol - EPS <= mid <= radius + EPS
    exact = width * height - (4 - math.pi) * radius ** 2
    assert polygon.is_valid and 0 <= exact - polygon.area < 0.01 * exact


def test_circle_is_inscribed():
    poly = circle_outline(1.5, TOL)
    check_rounded(poly, 1.5, 1.5, 0.75)
    # 1 µm rule at r = 0.75 mm: 2*pi / (2*acos(1 - 0.001/0.75)) = 60.8 -> 61 vertices.
    assert len(poly.exterior.coords) - 1 == 61


def test_rectangle_is_exact():
    poly = rect_outline(2.0, 0.5)
    assert poly.area == 1.0 and poly.bounds == (-1.0, -0.25, 1.0, 0.25)


@pytest.mark.parametrize("width,height,radius", [(1.2, 1.4, 0.25), (1.025, 1.4, 0.2135),
                                                  (3.0, 1.0, 0.5), (1.0, 3.0, 0.5), (0.8, 0.8, 0.1)])
def test_roundrect_corners_are_inscribed_quarter_circles(width, height, radius):
    check_rounded(roundrect_outline(width, height, radius, TOL), width, height, radius)


def test_roundrect_limits():
    assert roundrect_outline(1.0, 2.0, 0.0, TOL).equals(rect_outline(1.0, 2.0))
    # Radius half the short side: a stadium, built from four quarter circles (so its vertex
    # count, and area, differ a little from oval_outline's two half circles; both within 1 µm).
    check_rounded(roundrect_outline(1.0, 2.0, 0.5, TOL), 1.0, 2.0, 0.5)


@pytest.mark.parametrize("width,height", [(2.0, 1.7), (1.7, 2.0), (3.0, 0.6)])
def test_oval_is_a_stadium_on_the_long_axis(width, height):
    poly = oval_outline(width, height, TOL)
    check_rounded(poly, width, height, min(width, height) / 2)
    x0, y0, x1, y1 = poly.bounds
    assert (x1 - x0, y1 - y0) == pytest.approx((width, height), abs=2 * TOL)


def test_oval_with_equal_sides_is_a_circle():
    assert oval_outline(1.2, 1.2, TOL).equals(circle_outline(1.2, TOL))


@pytest.mark.parametrize("corner", CORNERS)
def test_each_chamfer_cuts_exactly_its_own_corner(corner):
    width, height, c = 1.025, 1.4, 0.205
    poly = chamfer_outline(width, height, c, {corner})
    assert poly.is_valid and poly.area == pytest.approx(width * height - c * c / 2, rel=1e-12)
    w, h = width / 2 - 0.01, height / 2 - 0.01
    # Written out here, not taken from the module, so a mixed-up corner table fails (Y up).
    probes = {"upperLeft": (-w, h), "upperRight": (w, h), "lowerLeft": (-w, -h), "lowerRight": (w, -h)}
    for name, probe in probes.items():
        assert poly.contains(Point(probe)) is (name != corner), name


def test_all_and_no_chamfers():
    assert chamfer_outline(1.0, 2.0, 0.2, set()).equals(rect_outline(1.0, 2.0))
    poly = chamfer_outline(1.0, 2.0, 0.5, set(CORNERS))   # adjacent cuts meet on the short sides
    assert poly.is_valid and len(poly.exterior.coords) - 1 == 6
    assert poly.area == pytest.approx(2.0 - 4 * 0.125)


def test_contour_is_kept_as_given():
    triangle_pad = [(1.0, 0.0), (0.5, -0.75), (-0.5, -0.75), (-0.5, 0.75), (0.5, 0.75)]
    poly = contour_outline(triangle_pad)
    assert list(poly.exterior.coords)[:-1] == triangle_pad
    with pytest.raises(ValueError, match="not a valid polygon"):
        contour_outline([(0, 0), (1, 1), (1, 0), (0, 1)])


def test_place_rotates_counter_clockwise_then_moves():
    # An L-shaped outline makes any rotation or mirror visible.
    ell = contour_outline([(0, 0), (2, 0), (2, 1), (1, 1), (1, 3), (0, 3)])
    moved = place(ell, 10.0, -5.0, 90.0)
    expected = [(10.0, -5.0), (10.0, -3.0), (9.0, -3.0), (9.0, -4.0), (7.0, -4.0), (7.0, -5.0)]
    assert [v for p in list(moved.exterior.coords)[:-1] for v in p] == \
        pytest.approx([v for p in expected for v in p], abs=1e-12)
    assert moved.area == pytest.approx(ell.area)
    # 30 degrees: the far end of the long leg turns towards +y and -x.
    tip = place(contour_outline([(0, -0.1), (3, -0.1), (3, 0.1), (0, 0.1)]), 0, 0, 30)
    assert tip.contains(Point(2.9 * math.cos(math.radians(30)), 2.9 * math.sin(math.radians(30))))


def test_place_keeps_holes():
    ring = contour_outline([(0, 0), (4, 0), (4, 4), (0, 4)]).difference(box(1, 1, 2, 2))
    moved = place(ring, 1.0, 1.0, 45.0)
    assert len(moved.interiors) == 1 and moved.area == pytest.approx(15.0)


@pytest.mark.parametrize("call,message", [
    (lambda: circle_outline(0, TOL), "diameter 0 must be positive"),
    (lambda: rect_outline(1, -1), "height -1 must be positive"),
    (lambda: roundrect_outline(1, 2, 0.6, TOL), "corner radius"),
    (lambda: roundrect_outline(1, 2, -0.1, TOL), "corner radius"),
    (lambda: oval_outline(0, 1, TOL), "width 0 must be positive"),
    (lambda: chamfer_outline(1, 2, 0.6, {"upperLeft"}), "chamfer 0.6"),
    (lambda: chamfer_outline(1, 2, 0.1, {"topLeft"}), "unknown chamfer corners"),
])
def test_rejections(call, message):
    with pytest.raises(ValueError, match=message):
        call()

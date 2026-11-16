"""Track outlines: inscribed polygons of round-ended segments and arcs (synthetic only)."""

import math

import pytest
from shapely.geometry import Point

from pdnlab_dc.track_outline import (arc_outline, arc_points, arc_sweep, segment_outline,
                                     steps_for)

TOL = 1e-3          # 1 µm in millimetres, as the reader will pass it
RADIUS_TOL = 1e-3
EPS = 1e-9          # floating-point slack, far below TOL

# The first +3.3V arc of tests/fixtures/arc_track, IPC frame (mm, Y up).
REAL_START, REAL_END, REAL_CENTRE = (143.085635, -77.825635), (141.530, -74.070), (146.84130, -74.070)


def distance_to_segment(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def distance_to_arc(p, start, end, centre, clockwise):
    """Distance to the true centre line: the arc between start and end in that direction."""
    a0, _, sweep = arc_sweep(start, end, centre, clockwise)
    radius = (math.dist(start, centre) + math.dist(end, centre)) / 2
    angle = math.atan2(p[1] - centre[1], p[0] - centre[0])
    along = (angle - a0) % (2 * math.pi) if sweep > 0 else (a0 - angle) % (2 * math.pi)
    if along <= abs(sweep):
        return abs(math.dist(p, centre) - radius)
    return min(math.dist(p, start), math.dist(p, end))


def check_inscribed(polygon, distance, half, tol, slack=EPS, outward=0.0):
    """Vertices on the true outline; no edge strays more than tol inside it or ``outward``
    outside it. Convex outlines (segments) have outward 0; an arc's inner edge is concave,
    so its chords bulge up to tol into the empty side (Erekle's choice A, 2026-11-16)."""
    ring = list(polygon.exterior.coords)
    for p in ring:
        assert distance(p) == pytest.approx(half, abs=slack)
    worst_in = worst_out = 0.0
    for p, q in zip(ring, ring[1:]):
        mid = distance(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2))
        worst_in, worst_out = max(worst_in, half - mid), max(worst_out, mid - half)
    assert worst_in <= tol + slack and worst_out <= outward + slack
    return worst_in, worst_out


@pytest.mark.parametrize("radius,sweep", [(0.25, math.pi), (5.0, math.pi / 4), (100.0, 2 * math.pi),
                                          (0.0015, math.pi), (1.0, 1e-6)])
def test_step_count_is_the_fewest_that_keeps_chord_error_within_tolerance(radius, sweep):
    n = steps_for(radius, sweep, TOL)
    sag = lambda steps: radius * (1 - math.cos(sweep / steps / 2))
    assert sag(n) <= TOL + EPS
    if n > 1:
        assert sag(n - 1) > TOL


def test_step_count_examples_and_rejections():
    assert steps_for(0.25, math.pi, TOL) == 18      # 0.5 mm track cap
    assert steps_for(0.0005, math.pi, TOL) == 1     # tolerance above the radius
    for bad in ((0, 1, TOL), (1, 1, 0), (-1, 1, TOL)):
        with pytest.raises(ValueError, match="must be positive"):
            steps_for(*bad)


def test_arc_points_hit_both_ends_exactly_in_count():
    pts = arc_points((1.0, 2.0), 3.0, 0.5, -1.2, TOL)
    assert len(pts) == steps_for(3.0, 1.2, TOL) + 1
    assert pts[0] == pytest.approx((1 + 3 * math.cos(0.5), 2 + 3 * math.sin(0.5)))
    assert pts[-1] == pytest.approx((1 + 3 * math.cos(-0.7), 2 + 3 * math.sin(-0.7)))


@pytest.mark.parametrize("start,end,width", [((10.0, -2.0), (16.0, -5.0), 0.5),
                                              ((-3.0, 7.0), (-3.0, 1.0), 0.2),
                                              ((0.0, 0.0), (0.05, 0.0), 2.0)])
def test_segment_is_an_inscribed_stadium(start, end, width):
    poly = segment_outline(start, end, width, TOL)
    assert poly.is_valid
    check_inscribed(poly, lambda p: distance_to_segment(p, start, end), width / 2, TOL)
    exact = math.dist(start, end) * width + math.pi * (width / 2) ** 2
    assert 0 < exact - poly.area < 0.01 * exact      # slightly less copper, never more
    # The track reaches both end points plus half the width, along its own direction.
    ux, uy = ((end[0] - start[0]) / math.dist(start, end), (end[1] - start[1]) / math.dist(start, end))
    tip = (end[0] + ux * (width / 2 - 2 * TOL), end[1] + uy * (width / 2 - 2 * TOL))
    assert poly.contains(Point(tip))


def test_zero_length_segment_is_an_inscribed_circle():
    poly = segment_outline((4.0, -1.0), (4.0, -1.0), 0.6, TOL)
    check_inscribed(poly, lambda p: math.dist(p, (4.0, -1.0)), 0.3, TOL)
    assert poly.area == pytest.approx(math.pi * 0.09, rel=0.01) and poly.area < math.pi * 0.09


def test_real_arc_from_the_fixture_matches_kicad_mid_point():
    poly = arc_outline(REAL_START, REAL_END, REAL_CENTRE, True, 0.5, TOL, RADIUS_TOL)
    assert poly.is_valid and len(poly.exterior.coords) - 1 == 77
    # KiCad's own mid point (arc_brd.kicad_pcb, Y negated) lies on the centre line.
    assert poly.contains(Point(141.934296, -76.102535))
    exact = math.pi / 4 * 5.3112956 * 0.5 + math.pi * 0.25 ** 2
    assert poly.area == pytest.approx(exact, rel=1e-3) and poly.area < exact
    # Radii differ by 9 nm, so edge vertices sit within half of that of the true band.
    check_inscribed(poly, lambda p: distance_to_arc(p, REAL_START, REAL_END, REAL_CENTRE, True),
                    0.25, TOL, slack=5e-6, outward=TOL)


@pytest.mark.parametrize("clockwise", [True, False])
def test_direction_flag_picks_the_short_or_the_long_way_round(clockwise):
    # Quarter circle from (5, 0) to (0, 5) round the origin: counter-clockwise is 90°,
    # clockwise is 270°. The two outlines must cover opposite sides.
    start, end, centre = (5.0, 0.0), (0.0, 5.0), (0.0, 0.0)
    poly = arc_outline(start, end, centre, clockwise, 0.4, TOL, RADIUS_TOL)
    assert poly.is_valid
    q1, q3 = Point(5 * math.cos(math.pi / 4), 5 * math.sin(math.pi / 4)), Point(-5 * 0.7071, -5 * 0.7071)
    assert poly.contains(q1) is not clockwise and poly.contains(q3) is clockwise
    sweep = 1.5 * math.pi if clockwise else 0.5 * math.pi
    exact = sweep * 5 * 0.4 + math.pi * 0.2 ** 2
    assert poly.area == pytest.approx(exact, rel=1e-3) and poly.area < exact
    _, out = check_inscribed(poly, lambda p: distance_to_arc(p, start, end, centre, clockwise),
                             0.2, TOL, outward=TOL)
    assert out > 0.5 * TOL  # the inner edge really does bulge; the test would see it


def test_asymmetric_arc_far_from_the_origin():
    centre, r = (37.0, -12.5), 3.0
    start = (centre[0] + r * math.cos(0.3), centre[1] + r * math.sin(0.3))
    end = (centre[0] + r * math.cos(2.0), centre[1] + r * math.sin(2.0))
    poly = arc_outline(start, end, centre, False, 0.25, TOL, RADIUS_TOL)
    check_inscribed(poly, lambda p: distance_to_arc(p, start, end, centre, False), 0.125, TOL,
                    outward=TOL)
    assert poly.area == pytest.approx(1.7 * r * 0.25 + math.pi * 0.125 ** 2, rel=1e-3)


def test_outlines_are_deterministic():
    a = arc_outline(REAL_START, REAL_END, REAL_CENTRE, True, 0.5, TOL, RADIUS_TOL)
    b = arc_outline(REAL_START, REAL_END, REAL_CENTRE, True, 0.5, TOL, RADIUS_TOL)
    assert list(a.exterior.coords) == list(b.exterior.coords)


@pytest.mark.parametrize("args,message", [
    (((0, 0), (1, 0), 0.0), "width 0.0 must be positive"),
    (((0, 0), (1, 0), -0.2), "must be positive"),
])
def test_segment_rejections(args, message):
    with pytest.raises(ValueError, match=message):
        segment_outline(*args, TOL)


@pytest.mark.parametrize("start,end,centre,width,message", [
    ((5.0, 0.0), (5.0, 0.0), (0.0, 0.0), 0.4, "coincide"),
    ((5.0, 0.0), (0.0, 5.002), (0.0, 0.0), 0.4, "not equidistant"),
    ((0.1, 0.0), (0.0, 0.1), (0.0, 0.0), 0.4, "fold over"),
    ((0.2, 0.0), (0.0, 0.2), (0.0, 0.0), 0.4, "fold over"),
    ((5.0, 0.0), (0.0, 5.0), (0.0, 0.0), 0.0, "must be positive"),
])
def test_arc_rejections(start, end, centre, width, message):
    with pytest.raises(ValueError, match=message):
        arc_outline(start, end, centre, False, width, TOL, RADIUS_TOL)

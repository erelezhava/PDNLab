"""Polygon outlines of round-ended tracks: straight segments and circular arcs.

Unit-free: coordinates, widths and tolerances share one unit (the IPC-2581
reader passes source millimetres and transforms the vertices afterwards).

Every curved piece is a true circle approximated by a polygon whose vertices
all lie on the circle, with no chord more than ``tol`` from it. Where the
copper is inside the circle (caps, the outer edge of an arc) the chords cut a
little copper off; on an arc's inner edge the copper is outside the circle, so
there the chords add up to ``tol`` of copper. The total area is still slightly
less than the exact outline, because the outer edge is longer than the inner
one and the caps also lose copper. (Choice A, approved by Erekle 2026-11-16.)
The vertex count of each piece follows from its own radius and sweep, so the
same rule serves track caps, arc edges and, later, round pads and via rings.

A segment is a rectangle plus two half-circle caps centred on its end points.
An arc track is built from four circles: the outer edge (radius R + w/2) and
the inner edge (R - w/2) around the arc centre, and half-circle caps of radius
w/2 centred exactly on the exported start and end points, where it joins the
next track. R is the mean of the start and end radii; the two may differ by up
to ``radius_tol`` because exporters round the centre.
"""

import math

from shapely.geometry import Polygon


def steps_for(radius, sweep, tol):
    """Fewest equal steps over ``sweep`` radians so no chord sags more than ``tol``."""
    if radius <= 0 or tol <= 0:
        raise ValueError(f"radius {radius} and tolerance {tol} must be positive")
    if tol >= radius:
        return max(1, math.ceil(abs(sweep) / math.pi))
    max_step = 2 * math.acos(1 - tol / radius)
    # A sweep that is an exact multiple of max_step can land a hair above it in
    # floating point; the 1e-12 keeps such cases at the smaller count.
    return max(1, math.ceil(abs(sweep) / max_step - 1e-12))


def arc_points(centre, radius, start_angle, sweep, tol):
    """Points on the circle from ``start_angle`` through ``sweep``, both ends included."""
    n = steps_for(radius, sweep, tol)
    cx, cy = centre
    return [(cx + radius * math.cos(start_angle + sweep * k / n),
             cy + radius * math.sin(start_angle + sweep * k / n)) for k in range(n + 1)]


def _check_width(width):
    if not width > 0:
        raise ValueError(f"track width {width} must be positive")


def segment_outline(start, end, width, tol):
    """Outline of a straight track with round ends; a zero-length track is a circle."""
    _check_width(width)
    half = width / 2
    (x0, y0), (x1, y1) = start, end
    length = math.hypot(x1 - x0, y1 - y0)
    if length == 0:
        return Polygon(arc_points(start, half, 0.0, 2 * math.pi, tol)[:-1])
    forward = math.atan2(y1 - y0, x1 - x0)
    left = forward + math.pi / 2
    # Left side forward, cap round the end clockwise, right side back, cap round the start.
    end_cap = arc_points(end, half, left, -math.pi, tol)
    start_cap = arc_points(start, half, left - math.pi, -math.pi, tol)
    return Polygon(end_cap + start_cap)


def arc_sweep(start, end, centre, clockwise):
    """Signed sweep in radians (negative = clockwise) from ``start`` to ``end``."""
    a0 = math.atan2(start[1] - centre[1], start[0] - centre[0])
    a1 = math.atan2(end[1] - centre[1], end[0] - centre[0])
    turn = (a0 - a1) % (2 * math.pi) if clockwise else (a1 - a0) % (2 * math.pi)
    return a0, a1, -turn if clockwise else turn


def arc_outline(start, end, centre, clockwise, width, tol, radius_tol):
    """Outline of a circular-arc track with round ends (see the module docstring)."""
    _check_width(width)
    if start == end:
        raise ValueError("arc start and end coincide; a full-circle track is not supported")
    r0 = math.hypot(start[0] - centre[0], start[1] - centre[1])
    r1 = math.hypot(end[0] - centre[0], end[1] - centre[1])
    if abs(r0 - r1) > radius_tol:
        raise ValueError(f"arc centre is not equidistant from start and end: radii {r0} and "
                         f"{r1} differ by more than {radius_tol}")
    radius, half = (r0 + r1) / 2, width / 2
    if radius <= half:
        raise ValueError(f"arc radius {radius} is not larger than half the width {half}; "
                         "the inner edge would fold over")
    a0, a1, sweep = arc_sweep(start, end, centre, clockwise)
    if sweep == 0:
        raise ValueError("arc has zero sweep")
    side = math.copysign(math.pi, sweep)  # caps turn the same way as the arc
    outer = arc_points(centre, radius + half, a0, sweep, tol)
    end_cap = arc_points(end, half, a1, side, tol)
    inner = arc_points(centre, radius - half, a1, -sweep, tol)
    start_cap = arc_points(start, half, a0 + side, side, tol)
    # Each piece's first point is kept and its last dropped: caps sit on the exact
    # end points, edges on the mean radius, so neighbours may differ by radius_tol/2.
    return Polygon(outer[:-1] + end_cap[:-1] + inner[:-1] + start_cap[:-1])

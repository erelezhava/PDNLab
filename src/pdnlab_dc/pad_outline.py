"""Polygon outlines of pad shapes, in the pad's own frame, then placed on the board.

Unit-free like ``track_outline``: sizes, positions and tolerances share one unit
(the IPC-2581 reader passes source millimetres and transforms vertices after).

Each shape is built centred on the origin, in a Y-up frame, as the IPC-2581
dictionary describes it; ``place`` then rotates it counter-clockwise by the
pad's angle and moves it to the pad's location. Rotation lives only in
``place`` so its direction is decided in one spot.

Round parts (circles, round-rect corners, oval ends) follow the track rule:
every vertex on the true circle, no chord more than ``tol`` inside it, so the
area is slightly below exact. Rectangles and chamfers are exact.
"""

import math

from shapely.geometry import Polygon

from pdnlab_dc.track_outline import arc_points, segment_outline

CORNERS = ("lowerRight", "upperRight", "upperLeft", "lowerLeft")  # counter-clockwise


def _positive(**sizes):
    for name, value in sizes.items():
        if not value > 0:
            raise ValueError(f"pad {name} {value} must be positive")


def _polygon(points):
    """Polygon from a ring, dropping a vertex that repeats its neighbour (shared ends)."""
    ring = []
    for p in points:
        if not ring or math.dist(p, ring[-1]) > 1e-12 * max(1.0, abs(p[0]) + abs(p[1])):
            ring.append(p)
    if len(ring) > 1 and math.dist(ring[0], ring[-1]) <= 1e-12 * max(1.0, abs(ring[0][0]) + abs(ring[0][1])):
        ring.pop()
    return Polygon(ring)


def circle_outline(diameter, tol):
    _positive(diameter=diameter)
    return Polygon(arc_points((0.0, 0.0), diameter / 2, 0.0, 2 * math.pi, tol)[:-1])


def rect_outline(width, height):
    _positive(width=width, height=height)
    w, h = width / 2, height / 2
    return Polygon([(w, -h), (w, h), (-w, h), (-w, -h)])


def roundrect_outline(width, height, radius, tol):
    """Rectangle with all four corners rounded by ``radius`` (0 gives a rectangle)."""
    _positive(width=width, height=height)
    if radius < 0 or radius > min(width, height) / 2:
        raise ValueError(f"corner radius {radius} must be between 0 and half the shorter "
                         f"side {min(width, height) / 2}")
    if radius == 0:
        return rect_outline(width, height)
    cx, cy = width / 2 - radius, height / 2 - radius
    ring = []
    for k, (sx, sy) in enumerate(((1, -1), (1, 1), (-1, 1), (-1, -1))):
        # Lower-right corner from -90 deg, then each next corner a quarter turn on.
        ring += arc_points((sx * cx, sy * cy), radius, (k - 1) * math.pi / 2, math.pi / 2, tol)
    return _polygon(ring)


def oval_outline(width, height, tol):
    """Stadium: half circles on the shorter side (a circle when width equals height)."""
    _positive(width=width, height=height)
    if width == height:
        return circle_outline(width, tol)
    a = abs(width - height) / 2
    ends = ((-a, 0.0), (a, 0.0)) if width > height else ((0.0, -a), (0.0, a))
    return segment_outline(*ends, min(width, height), tol)


def chamfer_outline(width, height, chamfer, corners):
    """Rectangle with a straight 45-degree cut of ``chamfer`` at each named corner."""
    _positive(width=width, height=height)
    unknown = set(corners) - set(CORNERS)
    if unknown:
        raise ValueError(f"unknown chamfer corners {sorted(unknown)}; expected {CORNERS}")
    if chamfer < 0 or chamfer > min(width, height) / 2:
        raise ValueError(f"chamfer {chamfer} must be between 0 and half the shorter side "
                         f"{min(width, height) / 2}")
    w, h = width / 2, height / 2
    square = dict(zip(CORNERS, ((w, -h), (w, h), (-w, h), (-w, -h))))
    ring = []
    for i, name in enumerate(CORNERS):
        x, y = square[name]
        if name not in corners or chamfer == 0:
            ring.append((x, y))
            continue
        for neighbour in (CORNERS[i - 1], CORNERS[(i + 1) % 4]):   # incoming, then outgoing
            nx, ny = square[neighbour]
            length = math.hypot(nx - x, ny - y)
            ring.append((x + chamfer * (nx - x) / length, y + chamfer * (ny - y) / length))
    return _polygon(ring)


def contour_outline(points):
    """Polygon from an exported custom or trapezoid pad outline, as given; never repaired."""
    polygon = Polygon(points)
    if not polygon.is_valid or polygon.area == 0:
        raise ValueError("custom pad outline is not a valid polygon")
    return polygon


def place(outline, x, y, rotation_deg):
    """Rotate a pad-frame outline counter-clockwise by ``rotation_deg``, then move it to (x, y)."""
    angle = math.radians(rotation_deg)
    c, s = math.cos(angle), math.sin(angle)
    def move(ring):
        return [(x + c * px - s * py, y + s * px + c * py) for px, py in ring]
    return Polygon(move(outline.exterior.coords), [move(r.coords) for r in outline.interiors])

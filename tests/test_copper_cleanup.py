"""Copper cleanup (copper_cleanup.clean_copper): merged pieces and cut holes.

The pieces of the public pad and thermal boards are compared with KiCad's own SVG
plot of each copper layer, with the same holes cut from the plot here (Shapely
circles, independent of the cleanup's hole outlines): same number of separate pieces,
area within curve-approximation error. Hand-computed areas check the cut radius
(finished radius + plating). Each rejection is made by changing one record of a real
board.
"""

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import pytest
from shapely import make_valid
from shapely.affinity import scale, translate
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from pdnlab_dc.board import CopperFeature
from pdnlab_dc.copper_cleanup import SCOPE, clean_copper
from pdnlab_dc.ipc2581_board import read_ipc2581_board

FIXTURES = Path(__file__).parent / "fixtures"
PLATING = 25e-6
# Area difference against KiCad's plot, whole layer; measured worst 0.382 % (thermal
# B.Cu: three small round pads, our 1 µm polygons against the plot's exact circles).
AREA_LIMIT = 0.005


def board(name):
    return read_ipc2581_board(next((FIXTURES / name).glob("*.xml")))


def svg_copper(path):
    """Union of all copper KiCad plotted on one layer (mm, Y down)."""
    shapes = []

    def walk(element, stroke_width):
        tag = element.tag.split("}")[-1]
        style = element.get("style", "")
        width = re.search(r"stroke-width:\s*([\d.]+)", style)
        stroke_width = float(width.group(1)) if width else stroke_width
        if tag == "circle":
            centre = Point(float(element.get("cx")), float(element.get("cy")))
            shapes.append(centre.buffer(float(element.get("r")), quad_segs=256))
        elif tag == "path":
            numbers = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", element.get("d"))]
            points = list(zip(numbers[0::2], numbers[1::2]))
            if "evenodd" in style or ("fill:#000000" in style and "stroke:none" in style):
                shapes.append(make_valid(Polygon(points)))   # fractured fills: reference side only
            else:
                shapes.append(LineString(points).buffer(stroke_width / 2, quad_segs=256))
        for child in element:
            walk(child, stroke_width)

    walk(ET.parse(path).getroot(), None)
    return unary_union(shapes)


@pytest.mark.parametrize("name", ["pad_shapes", "thermal_relief"])
def test_pieces_match_kicad_plot_with_holes_cut(name):
    b = board(name)
    clean = clean_copper(b, PLATING)
    holes = unary_union([
        Point(1000 * d.position_m[0], 1000 * d.position_m[1]).buffer(
            1000 * (d.diameter_m / 2 + (PLATING if d.plating == "plated" else 0.0)), quad_segs=256)
        for d in b.drills])
    for layer in b.layers:
        mine = [scale(p.geometry, 1000, 1000, origin=(0, 0)) for p in clean.pieces
                if p.layer_id == layer.id]
        svg = next((FIXTURES / name / "svg").glob(f"*-{layer.id.replace('.', '_')}.svg"))
        ref = svg_copper(svg).difference(holes)
        parts = [g for g in getattr(ref, "geoms", [ref]) if g.area > 1e-6]
        assert len(mine) == len(parts), layer.id                  # same separate pieces
        ours = unary_union(mine)
        assert ours.symmetric_difference(ref).area < AREA_LIMIT * ref.area, layer.id


def test_counts_on_the_public_boards():
    counts = {}
    for name in ("arc_track", "pad_shapes", "thermal_relief"):
        clean = clean_copper(board(name), PLATING)
        counts[name] = (len(clean.pieces), len(clean.cut_holes),
                        sum(p.net_id is None for p in clean.pieces), len(clean.removed))
    # pad_shapes has one track ending at a via centre, its stub inside the drilled wall.
    assert counts == {"arc_track": (2, 0, 0, 0), "pad_shapes": (30, 20, 1, 1),
                      "thermal_relief": (7, 12, 0, 0)}


def test_every_feature_is_in_a_piece_or_drilled_away_on_every_public_board():
    for name in ("arc_track", "pad_shapes", "thermal_relief"):
        b = board(name)
        clean = clean_copper(b, PLATING)
        placed = {f for p in clean.pieces for f in p.feature_ids}
        gone = {r.feature_id for r in clean.removed}
        assert not placed & gone
        assert placed | gone == {f.id for f in b.features}, name


def test_cut_radius_is_finished_radius_plus_plating_by_hand():
    clean = clean_copper(board("thermal_relief"), PLATING)
    pieces = {p.id: p for p in clean.pieces}
    # A lone 0.6 mm via pad on B.Cu with a 0.3 mm hole: ring from 0.15 + 0.025 to 0.3 mm.
    via = pieces["piece:B.Cu:GND:2"]
    assert via.geometry.area == pytest.approx(math.pi * (0.3e-3**2 - 0.175e-3**2), rel=0.005)
    # TP1 on B.Cu: 2 mm round pad, 1 mm hole: ring from 0.525 to 1 mm.
    tp1 = pieces["piece:B.Cu:GND:0"]
    assert tp1.geometry.area == pytest.approx(math.pi * (1e-3**2 - 0.525e-3**2), rel=0.005)
    assert len(tp1.geometry.interiors) == 1
    hole = Polygon(tp1.geometry.interiors[0])
    # Hole vertices on the true circle: the hole is at most 1 µm inside it.
    assert 1e-3 * 0.525 - 1e-6 < math.sqrt(hole.area / math.pi) < 0.525e-3
    # Doubling the plating cuts more copper, by exactly the larger ring.
    thicker = {p.id: p for p in clean_copper(board("thermal_relief"), 2 * PLATING).pieces}
    removed = via.geometry.area - thicker["piece:B.Cu:GND:2"].geometry.area
    assert removed == pytest.approx(math.pi * (0.2e-3**2 - 0.175e-3**2), rel=0.02)


def test_every_feature_belongs_to_a_piece_of_its_net_and_holes_are_recorded():
    b = board("thermal_relief")
    clean = clean_copper(b, PLATING)
    pieces = {p.id: p for p in clean.pieces}
    assert len(pieces) == len(clean.pieces)
    member = {fid: p for p in clean.pieces for fid in p.feature_ids}
    for f in b.features:
        assert f.id in member and member[f.id].net_id == f.net_id, f.id
    drills = {d.id: d for d in b.drills}
    for hole in clean.cut_holes:
        d = drills[hole.drill_id]
        assert hole.radius_m == pytest.approx(d.diameter_m / 2 + PLATING)
        # A plated hole has copper all round: exactly one piece, of the hole's net.
        (piece_id,) = hole.piece_ids
        assert hole.drill_id in pieces[piece_id].drill_ids
    # Every plated hole is recorded on every layer where it has pad or via copper.
    linked = {(f.drill_id, f.layer_id) for f in b.features if f.drill_id}
    assert {(h.drill_id, h.layer_id) for h in clean.cut_holes} == linked
    # Largest piece first: the GND plane is :0 on the inner layers.
    assert pieces["piece:In1.Cu:GND:0"].geometry.area > 1.7e-3


def test_no_net_copper_is_its_own_group():
    clean = clean_copper(board("pad_shapes"), PLATING)
    (lone,) = [p for p in clean.pieces if p.net_id is None]
    assert lone.id == "piece:F.Cu:none:0" and lone.feature_ids == ("pad:F.Cu:TP1.1",)


def test_assumptions():
    clean = clean_copper(board("arc_track"), PLATING)
    assert clean.assumptions[0] == SCOPE and "No barrels" in SCOPE
    assert clean.plating_m == PLATING and "25 µm" in clean.assumptions[1]


# Rejections, each made by changing one record of the real thermal board.

@pytest.fixture
def thermal():
    return board("thermal_relief")


def with_features(b, keep=lambda f: True, change=None, add=()):
    features = tuple((change(f) if change else f) for f in b.features if keep(f)) + tuple(add)
    return replace(b, features=features)


@pytest.mark.parametrize("plating", [None, 0, -25e-6, float("nan"), True, "25e-6"])
def test_plating_is_required(thermal, plating):
    with pytest.raises(ValueError, match="plating_m .* is required"):
        clean_copper(thermal, plating)


def test_two_nets_overlapping_is_a_short(thermal):
    # Move R1.1 (+3V3) onto TP2's pad, which is GND.
    tp2 = next(f for f in thermal.features if f.id == "pad:F.Cu:TP2.1").geometry.centroid
    def move(f):
        if f.id != "pad:F.Cu:R1.1":
            return f
        c = f.geometry.centroid
        return replace(f, geometry=translate(f.geometry, tp2.x - c.x, tp2.y - c.y))
    with pytest.raises(ValueError) as error:
        clean_copper(with_features(thermal, change=move), PLATING)
    message = str(error.value)
    assert message.startswith("F.Cu: copper of ") and "'+3V3' (pad:F.Cu:R1.1)" in message
    assert "'GND'" in message and message.endswith("a short (a net tie is not supported yet)")


def test_no_net_copper_overlapping_net_copper_raises(thermal):
    blob = CopperFeature("test:blob", None, "In1.Cu", "fill", box(0.200, 0.100, 0.201, 0.101))
    with pytest.raises(ValueError, match=r"In1\.Cu: copper of .*no net \(test:blob\).*a short$"):
        clean_copper(with_features(thermal, add=[blob]), PLATING)


def test_plated_hole_breaking_out_of_its_copper_raises(thermal):
    # TP1's B.Cu pad shrunk to 0.5 mm radius: smaller than the 0.525 mm drilled wall.
    def shrink(f):
        if f.id != "pad:B.Cu:TP1.1":
            return f
        return replace(f, geometry=f.geometry.centroid.buffer(0.5e-3, quad_segs=64))
    with pytest.raises(ValueError, match=r"B\.Cu: drill:H3 of 'GND' breaks out of its copper"):
        clean_copper(with_features(thermal, change=shrink), PLATING)


def test_plated_hole_wall_reaching_other_net_raises(thermal):
    # Remove one via's B.Cu pad and put a +3V3 sliver where its drilled wall passes.
    via = next(f for f in thermal.features if f.layer_id == "B.Cu" and f.kind == "via")
    x, y = via.geometry.centroid.coords[0]
    sliver = CopperFeature("test:sliver", "+3V3", "B.Cu", "track",
                           box(x + 0.16e-3, y - 0.05e-3, x + 0.4e-3, y + 0.05e-3))
    broken = with_features(thermal, keep=lambda f: f.id != via.id, add=[sliver])
    with pytest.raises(ValueError, match=rf"B\.Cu: {via.drill_id} of 'GND' reaches copper of "
                                         r"'\+3V3' at its drilled wall .*a short"):
        clean_copper(broken, PLATING)


def test_hole_copper_on_two_nets_raises(thermal):
    via = next(f for f in thermal.features if f.layer_id == "B.Cu" and f.kind == "via")
    broken = with_features(thermal, change=lambda f: replace(f, net_id="+3V3") if f.id == via.id else f)
    with pytest.raises(ValueError, match=rf"{via.drill_id}: its pad and via copper is on more "
                                         "than one net"):
        clean_copper(broken, PLATING)


def test_piece_under_one_square_micrometre_raises(thermal):
    speck = CopperFeature("test:speck", None, "B.Cu", "fill", box(0.190, 0.090, 0.190 + 0.5e-6,
                                                                0.090 + 1e-6))
    with pytest.raises(ValueError, match=r"piece:B\.Cu:none:0 .*piece of 0\.5 µm²"):
        clean_copper(with_features(thermal, add=[speck]), PLATING)


def test_unknown_plating_of_a_hole_raises(thermal):
    drills = tuple(replace(d, plating="unknown") if d.kind == "via" else d for d in thermal.drills)
    with pytest.raises(ValueError, match="plating 'unknown'; plated or nonplated is needed"):
        clean_copper(replace(thermal, drills=drills), PLATING)


def test_non_plated_hole_is_cut_at_its_own_radius(thermal):
    # A 2 mm non-plated hole drilled through the GND fill, away from everything else.
    from pdnlab_dc.board import DrillFeature
    fill = next(f for f in thermal.features if f.id == "fill:In1.Cu:0").geometry
    centre = fill.buffer(-2e-3).representative_point()
    hole = DrillFeature("drill:NPTH", (centre.x, centre.y), 2e-3, "nonplated", "F.Cu", "B.Cu",
                        "mechanical")
    before = {p.id: p.geometry.area for p in clean_copper(thermal, PLATING).pieces}
    clean = clean_copper(replace(thermal, drills=thermal.drills + (hole,)), PLATING)
    after = {p.id: p.geometry.area for p in clean.pieces}
    cut = {h.layer_id: h for h in clean.cut_holes if h.drill_id == "drill:NPTH"}
    assert sorted(cut) == ["F.Cu", "In1.Cu", "In2.Cu"]          # no copper there on B.Cu
    for layer in cut:
        assert cut[layer].radius_m == 1e-3                       # no plating added
        removed = before[f"piece:{layer}:GND:0"] - after[f"piece:{layer}:GND:0"]
        # Hole vertices on the true circle, at most 1 µm inside it.
        assert math.pi * (1e-3 - 1e-6)**2 < removed < math.pi * 1e-3**2


def test_track_stub_inside_a_via_hole_is_listed_as_drilled_away(thermal):
    # A 0.1 mm wide, 0.1 mm long stub at a via centre: inside its 0.175 mm drilled wall.
    via = next(f for f in thermal.features if f.layer_id == "F.Cu" and f.kind == "via")
    x, y = via.geometry.centroid.coords[0]
    stub = CopperFeature("test:stub", via.net_id, "F.Cu", "track",
                         LineString([(x - 0.05e-3, y), (x + 0.05e-3, y)]).buffer(0.05e-3))
    clean = clean_copper(with_features(thermal, add=[stub]), PLATING)
    assert [(r.feature_id, r.drill_id) for r in clean.removed] == [("test:stub", via.drill_id)]
    assert not any("test:stub" in p.feature_ids for p in clean.pieces)
    # The public boards have none.
    assert clean_copper(thermal, PLATING).removed == ()


def test_stub_reaching_past_the_drilled_wall_stays_copper(thermal):
    via = next(f for f in thermal.features if f.layer_id == "F.Cu" and f.kind == "via")
    x, y = via.geometry.centroid.coords[0]
    stub = CopperFeature("test:stub", via.net_id, "F.Cu", "track",
                         LineString([(x, y), (x + 0.25e-3, y)]).buffer(0.05e-3))
    clean = clean_copper(with_features(thermal, add=[stub]), PLATING)
    assert clean.removed == () and any("test:stub" in p.feature_ids for p in clean.pieces)

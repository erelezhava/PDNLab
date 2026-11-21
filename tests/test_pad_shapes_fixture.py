"""Pads read from a real KiCad 10 export against KiCad's own plot of the same board.

tests/fixtures/pad_shapes is a small public 4-layer board drawn by Erekle (2026-11-21):
round-rect, oval, circle, chamfered (one corner) and custom pads at 0, 15, 20, 30, 45
and 60 degrees, a through-hole connector, flipped copies on the back, three vias and a
pad with no net. pad_shapes.xml was exported with
``kicad-cli pcb export ipc2581 --version C --units mm --precision 6`` and svg/ with
``kicad-cli pcb export svg --layers F.Cu,In1.Cu,In2.Cu,B.Cu --mode-multi
--exclude-drawing-sheet --drill-shape-opt 0 --black-and-white``.

KiCad's SVG plot is drawn by KiCad's own plotter in board millimetres (Y down), so
matching it checks rotation direction, chamfer corners, back-side mirroring and sizes
independently of both the IPC-2581 exporter and this reader. Differences come only
from curve approximation (ours 1 µm, the plot about 5 µm, its circles exact).
"""

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from shapely.affinity import scale
from shapely.geometry import LineString, Point, Polygon

from pdnlab_dc.ipc2581_copper import read_ipc2581_copper

FIXTURE = Path(__file__).parent / "fixtures" / "pad_shapes"
MISMATCH_LIMIT = 0.01   # 1 % of the pad area; measured worst 0.433 % (a 0.6 mm via circle)


def svg_shapes(path, with_lines=True):
    """Filled polygons, circles and round-ended thick lines of a KiCad SVG plot (mm, Y down).

    With ``with_lines=False``, stroked lines narrower than 0.5 mm (tracks on this board;
    its only stroked pad is J1's 1.7 mm oval) are left out."""
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
            if "fill:#000000" in style and "stroke:none" in style:
                shapes.append(Polygon(points))
            elif with_lines or stroke_width >= 0.5:
                shapes.append(LineString(points).buffer(stroke_width / 2, quad_segs=256))
        for child in element:
            walk(child, stroke_width)

    walk(ET.parse(path).getroot(), None)
    return shapes


@pytest.fixture(scope="module")
def mismatches():
    copper = read_ipc2581_copper(FIXTURE / "pad_shapes.xml")
    result = {}
    for layer in (l.id for l in copper.copper_layers):
        drawn = svg_shapes(FIXTURE / "svg" / f"pad_shapes-{layer.replace('.', '_')}.svg")
        for feature in (p for p in copper.pads if p.layer_id == layer):
            ours = scale(feature.geometry, 1000, 1000, origin=(0, 0))  # metres -> mm, KiCad Y
            inside = ours.representative_point()
            ratios = [ours.symmetric_difference(s).area / ours.area for s in drawn if s.contains(inside)]
            result[feature.id] = (feature, min(ratios) if ratios else float("inf"), len(drawn))
    return result


def test_every_pad_and_via_matches_kicad_plot(mismatches):
    bad = {fid: round(100 * r, 3) for fid, (_, r, _) in mismatches.items() if r > MISMATCH_LIMIT}
    assert not bad, f"mismatch above {100 * MISMATCH_LIMIT} % of pad area: {bad}"


def test_counts_kinds_and_nets(mismatches):
    features = [f for f, _, _ in mismatches.values()]
    by_layer = {layer: sum(f.layer_id == layer for f in features)
                for layer in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")}
    # F.Cu: Q1 x3, R1 x2, R3 x2, JP1 x2, TP1, J1 x2 and 3 vias; inner layers: J1 x2 and 3 vias.
    # B.Cu: R2 x2, R4 x2, JP2 x2, J1 x2 and the 3 vias.
    assert by_layer == {"F.Cu": 15, "In1.Cu": 5, "In2.Cu": 5, "B.Cu": 11}
    assert sum(f.kind == "via" for f in features) == 12
    assert {f.id: f.net_id for f in features}["pad:F.Cu:TP1.1"] is None
    # Each layer's plot draws exactly as many pad and via shapes as were read (tracks aside).
    for layer, count in by_layer.items():
        drawn = svg_shapes(FIXTURE / "svg" / f"pad_shapes-{layer.replace('.', '_')}.svg", False)
        assert len(drawn) == count, layer


def test_asymmetric_pads_land_on_kicad_corners(mismatches):
    # One-corner chamfers, front (R3) and flipped back (R4), at 60 degrees: a wrong rotation
    # or mirror moves the cut to another corner (about 3 % mismatch), so these must be tight.
    for fid in ("pad:F.Cu:R3.1", "pad:B.Cu:R4.1"):
        assert mismatches[fid][1] < 1e-3, fid


# ---- Drill holes against the same board's .kicad_pcb ---------------------------------------
def kicad_vias_and_pin_drills():
    """Vias (position, drill, net, layers) and through-hole pad drills from the .kicad_pcb."""
    text = (FIXTURE / "pad_shapes.kicad_pcb").read_text()
    vias = []
    for body in re.findall(r"\n\t\(via\n(.*?)\n\t\)", text, re.S):
        x, y = (float(v) for v in re.search(r"\(at ([\d.-]+) ([\d.-]+)\)", body).groups())
        vias.append({"at": (x, y), "drill": float(re.search(r"\(drill ([\d.]+)\)", body).group(1)),
                     "net": re.search(r'\(net "([^"]*)"\)', body).group(1),
                     "layers": tuple(re.search(r"\(layers ([^)]*)\)", body).group(1).replace('"', "").split())})
    pins = []
    for fp in re.split(r"\n\t\(footprint ", text)[1:]:
        ref = re.search(r'\(property "Reference" "([^"]+)"', fp).group(1)
        for pad in re.findall(r'\n\t\t\(pad "([^"]*)" (thru_hole|np_thru_hole) .*?\n\t\t\)', fp, re.S):
            body = re.search(rf'\n\t\t\(pad "{re.escape(pad[0])}" {pad[1]} (.*?)\n\t\t\)', fp, re.S).group(1)
            pins.append({"pin": f"{ref}.{pad[0]}", "kind": pad[1],
                         "drill": float(re.search(r"\(drill ([\d.]+)\)", body).group(1)),
                         "net": (re.search(r'\(net "([^"]*)"\)', body) or [None, None])[1]})
    return vias, pins


def test_every_via_hole_matches_kicad_exactly():
    copper = read_ipc2581_copper(FIXTURE / "pad_shapes.xml")
    vias, _ = kicad_vias_and_pin_drills()
    drills = [d for d in copper.drills if d.kind == "via"]
    assert len(vias) == len(drills) == 3
    for via in vias:
        # KiCad's frame is Y down in millimetres; ours is Y down (reversed once) in metres.
        hit = [d for d in drills if math.dist(d.position_m, (via["at"][0] * 1e-3, via["at"][1] * 1e-3)) < 1e-9]
        assert len(hit) == 1, via
        d = hit[0]
        assert d.diameter_m == pytest.approx(via["drill"] * 1e-3, abs=1e-12)
        assert (d.plating, d.start_layer_id, d.end_layer_id) == ("plated",) + via["layers"]
        linked = [p for p in copper.pads if p.drill_id == d.id]
        assert sorted(p.layer_id for p in linked) == ["B.Cu", "F.Cu", "In1.Cu", "In2.Cu"]
        assert {p.kind for p in linked} == {"via"} and {p.net_id for p in linked} == {via["net"]}


def test_every_pin_hole_matches_kicad_and_sits_at_its_pad_centre():
    copper = read_ipc2581_copper(FIXTURE / "pad_shapes.xml")
    _, pins = kicad_vias_and_pin_drills()
    drills = {d.id: d for d in copper.drills if d.kind == "pin"}
    assert len(pins) == len(drills) == 2 and all(p["kind"] == "thru_hole" for p in pins)
    for pin in pins:
        linked = [p for p in copper.pads if p.id.endswith(f":{pin['pin']}")]
        assert sorted(p.layer_id for p in linked) == ["B.Cu", "F.Cu", "In1.Cu", "In2.Cu"]
        assert len({p.drill_id for p in linked}) == 1 and linked[0].drill_id in drills
        d = drills[linked[0].drill_id]
        assert d.diameter_m == pytest.approx(pin["drill"] * 1e-3, abs=1e-12)
        assert (d.plating, d.start_layer_id, d.end_layer_id) == ("plated", "F.Cu", "B.Cu")
        assert {p.net_id for p in linked} == {pin["net"]}
        # The pad shapes are checked against KiCad's plot above; the hole is at their centre.
        for p in linked:
            assert math.dist(p.geometry.centroid.coords[0], d.position_m) < 1e-9
    # No other pad or via on the board claims a hole.
    assert {p.drill_id for p in copper.pads} - {None} == {d.id for d in copper.drills}

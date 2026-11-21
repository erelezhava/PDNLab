"""Zone fills meeting pads and vias (thermal reliefs), against KiCad's own plot.

tests/fixtures/thermal_relief is a small public 4-layer board drawn by Erekle
(2026-11-21): a GND zone on F.Cu (thermal gap 0.5 mm, spokes 0.3 mm) and one on
In1.Cu and In2.Cu (gap 0.5 mm, spokes 0.5 mm) around a through-hole test point
TP1, a resistor R1 at 30 degrees with pad 2 on GND and pad 1 on +3V3, an SMD test
point TP2 set to a solid connection, and two GND vias. thermal_relief.xml and svg/
were exported as for tests/fixtures/pad_shapes.

Thermal reliefs are not pad properties in IPC-2581: KiCad writes the spokes as part
of the zone's fill polygon. These tests check that what the reader returns has the
connections KiCad filled: spokes overlapping their pads (not just touching), their
count and width, solid connections and clearances, and the fills matching KiCad's
SVG plot. The plot's fractured fill outlines are made valid with Shapely on the
reference side only, to measure areas; the reader never repairs geometry.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from shapely import make_valid
from shapely.affinity import scale
from shapely.geometry import Polygon
from shapely.ops import unary_union

from pdnlab_dc.ipc2581_copper import read_ipc2581_copper

FIXTURE = Path(__file__).parent / "fixtures" / "thermal_relief"
LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")


def mm(geometry):
    return scale(geometry, 1000, 1000, origin=(0, 0))   # metres -> mm, KiCad Y down


def kicad_zones():
    """{copper layer: (net, thermal gap mm, spoke width mm)} from the .kicad_pcb."""
    zones = {}
    text = (FIXTURE / "thermal_relief.kicad_pcb").read_text()
    for zone in re.findall(r"\n\t\(zone\n(.*?)\n\t\)", text, re.S):
        net = re.search(r'\(net "([^"]*)"\)', zone).group(1)
        layers = re.findall(r'"([^"]+\.Cu)"', re.search(r"\(layers? ([^)]*)\)", zone).group(1))
        gap = float(re.search(r"\(thermal_gap ([\d.]+)\)", zone).group(1))
        spoke = float(re.search(r"\(thermal_bridge_width ([\d.]+)\)", zone).group(1))
        for layer in layers:
            zones[layer] = (net, gap, spoke)
    return zones


def svg_fill(layer, min_area=20.0):
    """Union of the large even-odd filled outlines (zone fills) in KiCad's plot of a layer."""
    parts = []
    path = FIXTURE / "svg" / f"thermal_relief-{layer.replace('.', '_')}.svg"
    for element in ET.parse(path).getroot().iter():
        if element.tag.endswith("path") and "evenodd" in element.get("style", ""):
            numbers = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", element.get("d"))]
            outline = make_valid(Polygon(list(zip(numbers[0::2], numbers[1::2]))))
            if outline.area > min_area:
                parts.append(outline)
    return unary_union(parts)


def connection(fill, pad, gap):
    """How the fill meets a pad: (kind, spoke widths, overlap area in mm²).

    The ring from the pad's edge out to half the thermal gap holds only spokes for a
    thermal pad, all fill for a solid one, and nothing for a pad the zone clears."""
    ring = pad.buffer(gap / 2, quad_segs=64).difference(pad)
    crossing = fill.intersection(ring)
    pieces = [g for g in getattr(crossing, "geoms", [crossing]) if g.area > 1e-9]
    if not pieces:
        return "isolated", [], fill.intersection(pad).area
    if len(pieces) == 1 and pieces[0].area > 0.95 * ring.area:
        return "solid", [], fill.intersection(pad).area
    return "spokes", sorted(p.area / (gap / 2) for p in pieces), fill.intersection(pad).area


@pytest.fixture(scope="module")
def board():
    copper = read_ipc2581_copper(FIXTURE / "thermal_relief.xml")
    fills = {layer: unary_union([mm(f.geometry) for f in copper.fills if f.layer_id == layer])
             for layer in LAYERS}
    return copper, fills


def test_fills_match_kicad_plot_everywhere_and_around_every_pad(board):
    copper, fills = board
    for layer in LAYERS:
        ours, ref = fills[layer], svg_fill(layer)
        if layer == "B.Cu":
            assert ours.is_empty and ref.is_empty
            continue
        assert ours.symmetric_difference(ref).area < 1e-5 * ref.area, layer
        for pad in (p for p in copper.pads if p.layer_id == layer):
            window = mm(pad.geometry).buffer(0.6)
            a, b = ours.intersection(window), ref.intersection(window)
            assert a.symmetric_difference(b).area < 1e-3 * max(a.area, b.area), (layer, pad.id)


# What KiCad filled, per pad and layer: connection kind and number of spokes.
EXPECTED = {
    ("F.Cu", "pad:F.Cu:TP1.1"): ("spokes", 4),
    ("F.Cu", "pad:F.Cu:R1.2"): ("spokes", 3),   # the fourth would run into R1's +3V3 pad
    ("F.Cu", "pad:F.Cu:TP2.1"): ("solid", 0),
    ("F.Cu", "pad:F.Cu:R1.1"): ("isolated", 0),
    ("F.Cu", "via:F.Cu:0"): ("solid", 0), ("F.Cu", "via:F.Cu:1"): ("solid", 0),
    ("In1.Cu", "pad:In1.Cu:TP1.1"): ("spokes", 4),
    ("In1.Cu", "via:In1.Cu:0"): ("solid", 0), ("In1.Cu", "via:In1.Cu:1"): ("solid", 0),
    ("In2.Cu", "pad:In2.Cu:TP1.1"): ("spokes", 4),
    ("In2.Cu", "via:In2.Cu:0"): ("solid", 0), ("In2.Cu", "via:In2.Cu:1"): ("solid", 0),
}


def test_every_pad_and_via_meets_the_fill_as_kicad_filled_it(board):
    copper, fills = board
    zones = kicad_zones()
    seen = {}
    for pad in copper.pads:
        if pad.layer_id not in zones:
            continue
        net, gap, spoke = zones[pad.layer_id]
        kind, widths, overlap = connection(fills[pad.layer_id], mm(pad.geometry), gap)
        seen[(pad.layer_id, pad.id)] = (kind, len(widths))
        if kind == "spokes":
            assert pad.net_id == net
            # Spoke widths equal the zone's setting in the .kicad_pcb (0.3 or 0.5 mm).
            assert widths == pytest.approx([spoke] * len(widths), abs=0.01), (pad.id, widths)
        if kind in ("spokes", "solid"):
            # The fill runs into the pad: real overlap, so merging copper joins them.
            assert overlap > 0.05, (pad.id, overlap)
        else:
            assert pad.net_id != net and overlap == 0 and \
                fills[pad.layer_id].distance(mm(pad.geometry)) > 0.4, pad.id
    assert seen == EXPECTED


def test_zone_settings_and_fill_shapes_read(board):
    copper, _ = board
    assert kicad_zones() == {"F.Cu": ("GND", 0.5, 0.3), "In1.Cu": ("GND", 0.5, 0.5),
                             "In2.Cu": ("GND", 0.5, 0.5)}
    assert [(f.id, f.net_id) for f in copper.fills] == [
        ("fill:F.Cu:0", "GND"), ("fill:In1.Cu:0", "GND"), ("fill:In2.Cu:0", "GND")]
    # Every gap ring a fill leaves (thermal gaps cut by spokes, clearances) is a hole
    # recovered from KiCad's fractured outline; none is lost.
    assert [len(f.geometry.interiors) for f in copper.fills] == [4, 1, 1]

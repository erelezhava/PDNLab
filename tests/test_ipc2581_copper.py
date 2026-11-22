"""IPC-2581 copper reader (nets, zone fills, tracks): hand-written XML only, no real or
private boards.

The Set layout follows KiCad 10.0.8's exporter (pcbnew/pcb_io/ipc2581/pcb_io_ipc2581.cpp,
generateLayerSetNet): per copper layer and net one Set with Features/UserSpecial holding
fill Contours and track Lines/Arcs, via pads in that Set (padUsage="VIA") and pin pads in
a second Set. Copper without a net has no net attribute. Text, teardrops and footprint
graphics get their own Set with geometryUsage; board graphic shapes are referenced from
the user dictionary. Those must raise: copper on a net may not be left out of a result.
Fills are fractured: holes are reached along zero-width slits.
Coordinates are IPC (Y up); the stored frame reverses Y once and is in metres.
"""

from dataclasses import fields
import hashlib
import math

import pytest
from shapely.geometry import Point, Polygon

from pdnlab_dc.board import BoardModel
from pdnlab_dc.ipc2581_copper import (SCOPE, Ipc2581Copper, UnreadCount,
                                     parse_ipc2581_copper, read_ipc2581_copper)
from pdnlab_dc.ipc2581_metadata import NAMESPACE, XY_TRANSFORM, parse_ipc2581_metadata

LAYERS = ('<Layer name="F.Mask" layerFunction="SOLDERMASK"/>'
          '<Layer name="F.Cu" layerFunction="CONDUCTOR"/>'
          '<Layer name="DIELECTRIC_1" layerFunction="DIELCORE"/>'
          '<Layer name="B.Cu" layerFunction="CONDUCTOR"/>'
          '<Layer name="F.Cu_B.Cu" layerFunction="DRILL"><Span fromLayer="F.Cu" toLayer="B.Cu"/></Layer>')
STACKUP = ('<Stackup name="s"><StackupGroup name="g">'
           '<StackupLayer layerOrGroupRef="F.Mask" thickness="0.010" sequence="0"/>'
           '<StackupLayer layerOrGroupRef="F.Cu" thickness="0.035" sequence="1"/>'
           '<StackupLayer layerOrGroupRef="DIELECTRIC_1" thickness="1.51" sequence="2"/>'
           '<StackupLayer layerOrGroupRef="B.Cu" thickness="0.070" sequence="3"/>'
           '</StackupGroup></Stackup>')


def poly(tag, points):
    """IPC polygon element; the closing step back to the first point is added."""
    steps = [f'<PolyBegin x="{points[0][0]}" y="{points[0][1]}"/>']
    steps += [f'<PolyStepSegment x="{x}" y="{y}"/>' for x, y in points[1:] + points[:1]]
    return f"<{tag}>{''.join(steps)}</{tag}>"


def contour(points, *cutouts):
    return f"<Contour>{poly('Polygon', points)}{''.join(poly('Cutout', c) for c in cutouts)}</Contour>"


def special(*items, location=None):
    loc = f'<Location x="{location[0]}" y="{location[1]}"/>' if location else ""
    return f"<Features>{loc}<UserSpecial>{''.join(items)}</UserSpecial></Features>"


# Asymmetric outline far from the origin, IPC coordinates (mm); KiCad's Y is -y.
PWR_FILL = [(10, -2), (16, -2), (16, -3), (11, -7), (10, -7)]
PWR_FILL_M = [(0.010, 0.002), (0.016, 0.002), (0.016, 0.003), (0.011, 0.007), (0.010, 0.007)]
OUTER = [(0, 0), (20, 0), (20, -10), (0, -10)]
CUTOUT = [(4, -4), (6, -4), (6, -6), (4, -6)]
# KiCad fractured form: square 30..40 x 0..-10 with hole 34..36 x -4..-6, slit at y=-4.
FRACTURED = [(30, 0), (30, -4), (34, -4), (36, -4), (36, -6), (34, -6), (34, -4), (30, -4),
             (30, -10), (40, -10), (40, 0)]
FRACTURED_HOLE_M = {(0.034, 0.004), (0.036, 0.004), (0.036, 0.006), (0.034, 0.006)}
LINE = '<Line startX="16" startY="-2" endX="25" endY="-2"><LineDesc lineWidth="0.5" lineEnd="ROUND"/></Line>'
ARC = ('<Arc startX="1" startY="0" endX="0" endY="1" centerX="0" centerY="0" clockwise="false">'
       '<LineDesc lineWidth="0.25" lineEnd="ROUND"/></Arc>')
VIA_PAD = '<Pad padstackDefRef="V"><Location x="25" y="-2"/><StandardPrimitiveRef id="C1"/></Pad>'
PIN_PAD = ('<Pad padstackDefRef="P"><Location x="5" y="-5"/><StandardPrimitiveRef id="R1"/>'
           '<PinRef componentRef="U1" pin="3"/></Pad>')

F_CU = (f'<Set net="PWR" padUsage="VIA">{special(contour(PWR_FILL), LINE, ARC)}{VIA_PAD}</Set>'
        f'<Set net="PWR">{PIN_PAD}</Set>'
        f'<Set>{special(contour([(30, -1), (32, -1), (32, -3), (30, -3)]))}</Set>')
# Copper this reader must refuse rather than count: (Set, expected message).
TEXT_SET = ('<Set geometryUsage="TEXT"><NonstandardAttribute name="TEXT" value="V1" type="STRING"/>'
            f'{special(LINE, location=(0, 0))}</Set>')
TEARDROP_SET = ('<Set geometryUsage="TEARDROP" net="PWR">'
                f'{special(contour([(24, -1), (25, -2), (24, -3)]), location=(0, 0))}</Set>')
GRAPHIC_SETS = [
    ('<Set net="PWR"><Features><Location x="40" y="-5"/><UserPrimitiveRef id="URECT_1"/></Features></Set>',
     "board graphic shape on copper.*URECT_1"),
    ('<Set><Features><Location x="0" y="0"/><UserPrimitiveRef id="UPOLY_3"/></Features></Set>',
     "no net.*board graphic shape"),
    ('<Set geometryUsage="GRAPHIC" componentRef="U1"><Features><Location x="1" y="1"/>'
     '<UserPrimitiveRef id="UPOLY_2"/></Features></Set>', "footprint graphic on copper"),
    (TEXT_SET, "copper text"),
    (TEARDROP_SET, "net 'PWR'.*teardrop"),
]
B_CU = (f'<Set net="GND">{special(contour(OUTER, CUTOUT), contour(FRACTURED))}</Set>'
        f'<Set net="PWR" padUsage="VIA">{special(LINE)}{VIA_PAD}</Set>')
# H1: the via hole under the PWR via pads at (25, -2) on F.Cu and B.Cu. H2: a non-plated
# hole with no copper, the only place net SIG appears.
TOLS = 'plusTol="0.0" minusTol="0.0"'
DRILL = (f'<Set geometry="V" net="PWR"><Hole name="H1" diameter="0.30" platingStatus="VIA" {TOLS} x="25" y="-2"/></Set>'
         f'<Set geometry="M" net="SIG"><Hole name="H2" diameter="2.6" platingStatus="NONPLATED" {TOLS} x="40" y="-8"/></Set>')


def layer_feature(layer, content):
    return f'<LayerFeature layerRef="{layer}">{content}</LayerFeature>'


# Pad shapes the synthetic boards refer to (DictionaryStandard, millimetres).
SHAPES = ('<EntryStandard id="C1"><Circle diameter="0.6"/></EntryStandard>'
          '<EntryStandard id="R1"><RectCenter width="1.0" height="0.6"/></EntryStandard>')


def export(f_cu=F_CU, b_cu=B_CU, drill=DRILL, *, step=None, step_extra="", revision="C",
           shapes=SHAPES):
    if step is None:
        step = (f'<Step name="board"><Datum x="0.0" y="0.0"/>{step_extra}'
                f'{layer_feature("F.Cu", f_cu)}{layer_feature("B.Cu", b_cu)}'
                f'{layer_feature("F.Cu_B.Cu", drill)}</Step>')
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<IPC-2581 revision="{revision}" '
            f'xmlns="{NAMESPACE}"><Content><DictionaryStandard units="MILLIMETER">{shapes}'
            f'</DictionaryStandard></Content>'
            f'<Ecad name="Design"><CadHeader units="MILLIMETER"/>'
            f'<CadData>{LAYERS}{STACKUP}{step}</CadData></Ecad></IPC-2581>').encode()


def by_id(result):
    return {f.id: f for f in result.fills}


def test_fill_coordinates_are_reversed_in_y_once_and_scaled_to_metres_once():
    fill = by_id(parse_ipc2581_copper(export()))["fill:F.Cu:0"]
    assert (fill.net_id, fill.layer_id, fill.kind) == ("PWR", "F.Cu", "fill")
    # Every vertex: x = mm/1000, y = -mm/1000. A missing or doubled reversal gives negative y.
    coords = [v for point in fill.geometry.exterior.coords for v in point]
    assert coords == pytest.approx([v for point in PWR_FILL_M + PWR_FILL_M[:1] for v in point], abs=1e-15)
    assert fill.geometry.area == pytest.approx(20e-6, rel=1e-12)
    x0, y0, x1, y1 = fill.geometry.bounds
    assert (x0, y0, x1, y1) == pytest.approx((0.010, 0.002, 0.016, 0.007), abs=1e-15)
    # Wide at KiCad y = 2..3 mm, narrowing to the left towards y = 7 mm: a Y mirror swaps these.
    assert fill.geometry.contains(Polygon([(0.0155, 0.0025), (0.0159, 0.0025), (0.0159, 0.0028)]))
    assert not fill.geometry.intersects(Polygon([(0.0155, 0.0065), (0.0159, 0.0065), (0.0159, 0.0069)]))


def test_explicit_cutout_is_kept_as_a_hole():
    fill = by_id(parse_ipc2581_copper(export()))["fill:B.Cu:0"]
    assert fill.net_id == "GND" and len(fill.geometry.interiors) == 1
    assert fill.geometry.area == pytest.approx(196e-6, rel=1e-12)
    assert {tuple(round(v, 12) for v in p) for p in fill.geometry.interiors[0].coords} == \
        {(0.004, 0.004), (0.006, 0.004), (0.006, 0.006), (0.004, 0.006)}


def test_kicad_fractured_fill_is_split_into_outline_and_hole_without_moving_vertices():
    raw = Polygon([(x, y) for x, y in FRACTURED])
    assert not raw.is_valid  # why the slit form needs handling: Shapely rejects it as given
    fill = by_id(parse_ipc2581_copper(export()))["fill:B.Cu:1"]
    assert len(fill.geometry.interiors) == 1 and fill.geometry.is_valid
    assert fill.geometry.area == pytest.approx(96e-6, rel=1e-12) == raw.area * 1e-6
    assert {tuple(round(v, 12) for v in p) for p in fill.geometry.interiors[0].coords} == FRACTURED_HOLE_M
    outline = {tuple(round(v, 12) for v in p) for p in fill.geometry.exterior.coords}
    assert outline == {(0.030, 0.0), (0.030, 0.004), (0.030, 0.010), (0.040, 0.010), (0.040, 0.0)}


def test_two_slits_in_a_chain_and_a_ring_starting_inside_a_hole():
    # Hole 2 (a diamond) is reached from hole 1's right edge, hole 1 from the outline.
    ring = [(0, 0), (0, -4), (10, -4), (12, -4), (12, -5), (20, -5), (21, -4), (22, -5), (21, -6),
            (20, -5), (12, -5), (12, -6), (10, -6), (10, -4), (0, -4), (0, -10), (30, -10), (30, 0)]
    for start in (0, 6):
        rotated = ring[start:] + ring[:start]
        fill = parse_ipc2581_copper(export(b_cu=f'<Set net="GND">{special(contour(rotated))}</Set>')).fills[-1]
        assert len(fill.geometry.interiors) == 2
        assert fill.geometry.area == pytest.approx((300 - 4 - 2) * 1e-6, rel=1e-12)
        assert fill.geometry.bounds == pytest.approx((0, 0, 0.030, 0.010), abs=1e-15)


def test_nets_come_from_every_set_and_unassigned_copper_has_no_net():
    result = parse_ipc2581_copper(export())
    # SIG appears only on the drill layer; no net is invented for the unassigned fill.
    assert [(n.id, n.name) for n in result.nets] == [("GND", "GND"), ("PWR", "PWR"), ("SIG", "SIG")]
    assert [(f.id, f.layer_id, f.net_id) for f in result.fills] == [
        ("fill:F.Cu:0", "F.Cu", "PWR"), ("fill:F.Cu:1", "F.Cu", None),
        ("fill:B.Cu:0", "B.Cu", "GND"), ("fill:B.Cu:1", "B.Cu", "GND")]


def test_feature_ids_and_source_references_are_stable_and_traceable():
    first, second = (parse_ipc2581_copper(export()) for _ in range(2))
    assert [(f.id, f.source_ref) for f in first.fills] == [(f.id, f.source_ref) for f in second.fills]
    assert [f.source_ref for f in first.fills] == [
        "LayerFeature[@layerRef='F.Cu']/Set[1]/Features[1]/UserSpecial[1]/Contour[1]",
        "LayerFeature[@layerRef='F.Cu']/Set[3]/Features[1]/UserSpecial[1]/Contour[1]",
        "LayerFeature[@layerRef='B.Cu']/Set[1]/Features[1]/UserSpecial[1]/Contour[1]",
        "LayerFeature[@layerRef='B.Cu']/Set[1]/Features[1]/UserSpecial[1]/Contour[2]"]


def test_unread_copper_is_counted_not_read_and_other_layers_are_listed():
    result = parse_ipc2581_copper(export())
    assert result.unread == ()   # pads and vias are read since the pad increment
    assert result.uninspected_layers == ()   # the drill layer is read since the drills increment
    assert len(result.fills) == 4


@pytest.mark.parametrize("extra,message", GRAPHIC_SETS)
def test_copper_no_increment_reads_raises_instead_of_being_counted(extra, message):
    # Appended after the readable Sets, so it is Set[4] on F.Cu.
    with pytest.raises(ValueError, match=message) as error:
        parse_ipc2581_copper(export(f_cu=F_CU + extra))
    assert "layer 'F.Cu'" in str(error.value) and "Set[4]" in str(error.value)


def test_result_is_scoped_and_not_a_board_model():
    data = export()
    result = parse_ipc2581_copper(data)
    meta = parse_ipc2581_metadata(data)
    assert isinstance(result, Ipc2581Copper) and not isinstance(result, BoardModel)
    assert {f.name for f in fields(result)} == {"source", "copper_layers", "nets", "fills", "tracks",
                                                "pads", "pad_pins", "drills", "component_layers", "unread", "uninspected_layers",
                                                "scope"}
    assert result.scope == SCOPE and "not a complete board" in SCOPE
    assert "not cut" in SCOPE and "no barrel" in SCOPE   # what the result still leaves out
    assert result.source.assumptions[0] == SCOPE
    assert any("applied once" in a for a in result.source.assumptions)
    assert not any("has not been applied" in a for a in result.source.assumptions)
    # Header, units, layers and provenance come from the metadata reader unchanged.
    assert result.copper_layers == meta.copper_layers
    assert result.source.input_sha256 == meta.source.input_sha256 == hashlib.sha256(data).hexdigest()
    assert result.source.xy_transform == XY_TRANSFORM
    assert result.source.assumptions[-2:] == meta.source.assumptions[-2:]


def test_copper_layer_without_features_and_board_without_fills():
    no_via_hole = DRILL[DRILL.index('<Set geometry="M"'):]   # keep only the non-plated H2
    result = parse_ipc2581_copper(export(f_cu=f'<Set net="PWR">{PIN_PAD}</Set>', b_cu="", drill=no_via_hole))
    assert result.fills == () and [n.id for n in result.nets] == ["PWR", "SIG"]
    step = '<Step name="board"></Step>'
    result = parse_ipc2581_copper(export(step=step))
    assert (result.nets, result.fills, result.unread, result.uninspected_layers) == ((), (), (), ())


def test_read_from_path_hashes_the_bytes_parsed(tmp_path):
    path = tmp_path / "board.xml"
    path.write_bytes(export())
    assert read_ipc2581_copper(path) == parse_ipc2581_copper(export())


def fill_on_f_cu(content):
    return export(f_cu=f'<Set net="PWR">{content}</Set>')


SQUARE = [(0, 0), (4, 0), (4, -4), (0, -4)]
BOWTIE = [(0, 0), (6, -4), (6, 0), (0, -2)]
# A Contour in another namespace is not the IPC element and must not pass as one.
FOREIGN = special(contour(SQUARE)).replace("<Contour>", "<x:Contour xmlns:x='urn:x'>").replace(
    "</Contour>", "</x:Contour>")
CROSSING = [(0, 0), (4, 0), (4, -4), (0, -4), (4, 0), (0, 0), (0, -4), (4, -4)]


@pytest.mark.parametrize("data,message", [
    # Curved steps, in the outline and in a cutout.
    (fill_on_f_cu(special(contour(SQUARE).replace('<PolyStepSegment x="4" y="0"/>',
                                                  '<PolyStepCurve x="4" y="0" centerX="2" centerY="0" clockwise="true"/>'))),
     "PolyStepCurve.*curved"),
    (fill_on_f_cu(special(contour(OUTER, CUTOUT).replace('<PolyStepSegment x="6" y="-4"/>',
                                                         '<PolyStepCurve x="6" y="-4" centerX="5" centerY="-4" clockwise="true"/>'))),
     "curved"),
    # Transforms and offsets on any containing element.
    (fill_on_f_cu(special(contour(SQUARE)).replace("<Features>", '<Features><Xform rotation="90"/>')),
     "Xform in Features"),
    (export(f_cu=f'<Set net="PWR"><Xform rotation="90"/>{special(contour(SQUARE))}</Set>'), "Xform in a Set"),
    (fill_on_f_cu(special(contour(SQUARE)).replace("<UserSpecial>", '<UserSpecial><Xform mirror="true"/>')),
     "Xform in UserSpecial"),
    (fill_on_f_cu(special(contour(SQUARE), location=(1, 0))), "nonzero Location"),
    (fill_on_f_cu(special(contour(SQUARE), location=(0, "0.000001"))), "nonzero Location"),
    (fill_on_f_cu(special(contour(SQUARE)).replace("<Contour>", '<Contour><Location x="0" y="0"/>')),
     "Contour must hold one Polygon"),
    # Other content inside fill elements.
    (fill_on_f_cu(special(contour(SQUARE)).replace("</Polygon>", '<FillDesc fillProperty="HOLLOW"/></Polygon>')),
     "FillDesc"),
    (fill_on_f_cu(special(contour(SQUARE)).replace("</Polygon>", '<LineDescRef id="L1"/></Polygon>')),
     "LineDescRef"),
    (fill_on_f_cu(special(contour(SQUARE)).replace("<Polygon>", '<Polygon id="p">')), "attributes"),
    (fill_on_f_cu(special('<Polyline><PolyBegin x="0" y="0"/><PolyStepSegment x="1" y="0"/></Polyline>')),
     "Polyline in UserSpecial"),
    (fill_on_f_cu(special(contour(SQUARE).replace('<PolyStepSegment x="0" y="0"/></Polygon>', '</Polygon>'))),
     "closed ring"),
    (fill_on_f_cu(special(contour([(0, 0), (4, 0)]))), "closed ring"),
    (fill_on_f_cu(special(contour(SQUARE).replace('x="4" y="0"', 'x="nan" y="0"'))), "not a decimal"),
    (fill_on_f_cu(special(contour(SQUARE).replace('x="4" y="0"', 'y="0"'))), "exactly x and y"),
    # Invalid geometry raises instead of being repaired.
    (fill_on_f_cu(special(contour(BOWTIE))), "invalid fill polygon"),
    (fill_on_f_cu(special(contour(SQUARE, [(10, -1), (11, -1), (11, -2), (10, -2)]))), "invalid fill polygon"),
    (fill_on_f_cu(special(contour([(0, 0), (4, 0), (4, 0), (4, -4), (0, -4)]))), "zero-length edge"),
    (fill_on_f_cu(special(contour([(0, 0), (4, 0), (4, -4), (0, 0), (4, 0), (0, -4)]))), "occurs twice"),
    (fill_on_f_cu(special(contour(CROSSING))), "cross"),
    (fill_on_f_cu(special(contour([(0, 0), (4, 0), (8, 0), (4, 0), (4, -4), (0, -4)]))), "degenerate"),
    (fill_on_f_cu(special(contour([(0, 0), (4, 0), (8, 0)]))), "zero area"),
    # Sets this reader cannot assign or does not know.
    (export(f_cu=f'<Set componentRef="U1">{special(contour(SQUARE), location=(0, 0))}</Set>'),
     "owned by component 'U1'.*omits its net"),
    (export(f_cu=f'<Set net="PWR" polarity="NEGATIVE">{special(contour(SQUARE))}</Set>'), "polarity"),
    (export(f_cu=f'<Set geometryUsage="THIEVING">{special(contour(SQUARE))}</Set>'), "THIEVING"),
    (export(f_cu=f'<Set net="PWR" padUsage="TERMINATION">{PIN_PAD}</Set>'), "TERMINATION"),
    (export(f_cu=f'<Set net="PWR"><Set net="PWR">{special(contour(SQUARE))}</Set></Set>'),
     "Features in a nested Set"),
    (export(f_cu=f'<Set net="PWR">{special(contour(SQUARE)).replace("Features", "Feature")}</Set>'),
     "Feature in a Set"),
    (export(f_cu='<Set net="PWR"><Hole name="H" diameter="1" platingStatus="VIA" x="0" y="0"/></Set>'),
     "Hole in a Set"),
    (export(f_cu='<Set net="PWR">' + FOREIGN + '</Set>'), "urn:x"),
    (export(f_cu=f'{special(contour(SQUARE))}'), "Features in a copper LayerFeature"),
    (export(f_cu=f'<Set net=" ">{special(contour(SQUARE))}</Set>'), "blank net name"),
    # Layer and Step structure.
    (export(step_extra=layer_feature("F.Cu", "")), "more than one LayerFeature"),
    (export(step_extra=layer_feature("In9.Cu", "")), "In9.Cu.*not defined"),
    (export(step_extra='<LogicalNet name="PWR"/>'), "LogicalNet"),
    (export(step_extra='<PhyNetGroup name="g"/>'), "PhyNetGroup"),
    (export().replace(b"</CadData>", b'<Step name="second"/></CadData>'), "exactly one Step"),
    (export().replace(b'<Step name="board">', b"<Steps>").replace(b"</Step>", b"</Steps>"), "exactly one Step"),
    # The metadata reader's checks apply unchanged.
    (export(revision="B"), "revision C"),
])
def test_unsupported_content_raises_with_context(data, message):
    with pytest.raises(ValueError, match=message):
        parse_ipc2581_copper(data)


@pytest.mark.parametrize("content,net", [
    (f'<Set net="PWR">{special(contour(SQUARE), location=(2, 0))}</Set>', "net 'PWR'"),
    (f'<Set>{special(contour(BOWTIE))}</Set>', "no net"),
])
def test_errors_name_layer_net_and_element(content, net):
    with pytest.raises(ValueError) as error:
        parse_ipc2581_copper(export(f_cu=content))
    assert "layer 'F.Cu'" in str(error.value) and net in str(error.value)
    assert "LayerFeature[@layerRef='F.Cu']/Set[1]/Features[1]" in str(error.value)


def test_footprint_set_without_copper_is_accepted_and_nothing_counted():
    result = parse_ipc2581_copper(export(f_cu='<Set componentRef="U1"><Features><Location x="0" y="0"/>'
                                             '<UserSpecial/></Features></Set>'))
    assert not [u for u in result.unread if u.layer_id == "F.Cu"]
    with pytest.raises(ValueError, match="component Set content"):
        parse_ipc2581_copper(export(f_cu=f'<Set componentRef="U1">{PIN_PAD}</Set>'))


# ---- Tracks -------------------------------------------------------------------------------
# Asymmetric straight segment and a counter-clockwise quarter arc, far from the origin (IPC mm).
def line(x0, y0, x1, y1, width="0.4", end="ROUND"):
    return (f'<Line startX="{x0}" startY="{y0}" endX="{x1}" endY="{y1}">'
            f'<LineDesc lineWidth="{width}" lineEnd="{end}"/></Line>')


def arc(x0, y0, x1, y1, cx, cy, clockwise="false", width="0.4"):
    return (f'<Arc startX="{x0}" startY="{y0}" endX="{x1}" endY="{y1}" centerX="{cx}" '
            f'centerY="{cy}" clockwise="{clockwise}"><LineDesc lineWidth="{width}" lineEnd="ROUND"/></Arc>')


SEGMENT = line(12, -3, 18, -5)
QUARTER = arc(33, -10, 30, -7, 30, -10)   # IPC: from angle 0 to 90 degrees, counter-clockwise


def tracks_of(content, net='net="PWR"'):
    return parse_ipc2581_copper(export(f_cu=f"<Set {net}>{special(content)}</Set>", b_cu="",
                                       drill="")).tracks


def test_track_vertices_are_the_outline_transformed_exactly_once():
    from pdnlab_dc.track_outline import segment_outline
    track, = tracks_of(SEGMENT)
    in_mm = segment_outline((12.0, -3.0), (18.0, -5.0), 0.4, 1e-3)
    expected = [(x * 1e-3, -y * 1e-3) for x, y in in_mm.exterior.coords]
    assert [v for p in track.geometry.exterior.coords for v in p] == \
        pytest.approx([v for p in expected for v in p], abs=1e-15)


def test_segment_track_in_metres_with_y_reversed_once():
    from shapely.geometry import LineString
    track, = tracks_of(SEGMENT)
    assert (track.id, track.net_id, track.layer_id, track.kind) == ("track:F.Cu:0", "PWR", "F.Cu", "track")
    centre_line = LineString([(0.012, 0.003), (0.018, 0.005)])   # KiCad Y down, in metres
    for p in track.geometry.exterior.coords:
        assert centre_line.distance(Point(p)) == pytest.approx(0.0002, abs=1e-12)
    length = math.hypot(0.006, 0.002)
    assert track.geometry.area == pytest.approx(length * 0.0004 + math.pi * 0.0002 ** 2, rel=1e-3)


def test_arc_track_follows_the_direction_flag_in_ipc_coordinates():
    track, = tracks_of(QUARTER)
    # IPC 45 degrees on the arc -> metres with Y reversed; the long way round is not covered.
    on_arc = (0.030 + 0.003 * math.cos(math.pi / 4), 0.010 - 0.003 * math.sin(math.pi / 4))
    other_way = (0.030 - 0.003 * math.cos(math.pi / 4), 0.010 + 0.003 * math.sin(math.pi / 4))
    assert track.geometry.contains(Point(on_arc)) and not track.geometry.intersects(Point(other_way))
    for p in track.geometry.exterior.coords:   # all copper within the band around radius 3 mm
        assert 0.0028 - 1e-12 <= math.dist(p, (0.030, 0.010)) <= 0.0032 + 1e-12 or \
            min(math.dist(p, (0.033, 0.010)), math.dist(p, (0.030, 0.007))) == pytest.approx(0.0002)
    exact = math.pi / 2 * 0.003 * 0.0004 + math.pi * 0.0002 ** 2
    assert track.geometry.area == pytest.approx(exact, rel=1e-3) and track.geometry.area < exact
    flipped, = tracks_of(QUARTER.replace('clockwise="false"', 'clockwise="true"'))
    assert flipped.geometry.contains(Point(other_way)) and not flipped.geometry.intersects(Point(on_arc))


def test_tracks_ids_paths_nets_and_unassigned_copper():
    result = parse_ipc2581_copper(export())
    assert [(t.id, t.net_id, t.source_ref) for t in result.tracks] == [
        ("track:F.Cu:0", "PWR", "LayerFeature[@layerRef='F.Cu']/Set[1]/Features[1]/UserSpecial[1]/Line[1]"),
        ("track:F.Cu:1", "PWR", "LayerFeature[@layerRef='F.Cu']/Set[1]/Features[1]/UserSpecial[1]/Arc[1]"),
        ("track:B.Cu:0", "PWR", "LayerFeature[@layerRef='B.Cu']/Set[2]/Features[1]/UserSpecial[1]/Line[1]")]
    track, = tracks_of(SEGMENT, net="")
    assert track.net_id is None
    # Fills keep their own numbering beside tracks.
    assert [f.id for f in result.fills] == ["fill:F.Cu:0", "fill:F.Cu:1", "fill:B.Cu:0", "fill:B.Cu:1"]


def test_width_comes_from_the_line_desc():
    narrow, = tracks_of(line(12, -3, 18, -5, width="0.2"))
    wide, = tracks_of(line(12, -3, 18, -5, width="0.8"))
    assert wide.geometry.area / narrow.geometry.area == pytest.approx(
        (6.3246 * 0.8 + math.pi * 0.16) / (6.3246 * 0.2 + math.pi * 0.01), rel=1e-3)


@pytest.mark.parametrize("content,message", [
    (SEGMENT.replace('<LineDesc lineWidth="0.4" lineEnd="ROUND"/>', '<LineDescRef id="L1"/>'),
     "exactly one inline LineDesc"),
    (SEGMENT.replace('<LineDesc lineWidth="0.4" lineEnd="ROUND"/>', ''), "exactly one inline LineDesc"),
    (SEGMENT.replace('/></Line>', '/><LineDesc lineWidth="0.4" lineEnd="ROUND"/></Line>'),
     "exactly one inline LineDesc"),
    (line(12, -3, 18, -5, end="SQUARE"), "lineEnd 'SQUARE'.*only ROUND"),
    (SEGMENT.replace('lineEnd="ROUND"', 'lineEnd="ROUND" lineProperty="DASHED"'), "exactly lineWidth and lineEnd"),
    (SEGMENT.replace("<Line ", '<Line id="t1" '), "attributes must be exactly"),
    (line(12, -3, 18, -5, width="0"), "width 0.0 must be positive"),
    (line(12, -3, 18, -5, width="wide"), "not a decimal"),
    (QUARTER.replace(' clockwise="false"', ''), "attributes must be exactly"),
    (QUARTER.replace('clockwise="false"', 'clockwise="yes"'), "clockwise must be 'true' or 'false'"),
    (arc(33, -10, 30, -6.995, 30, -10), "not equidistant"),
    (arc(30.1, -10, 30, -9.9, 30, -10), "fold over"),
    (arc(33, -10, 33, -10, 30, -10), "coincide"),
])
def test_unsupported_tracks_raise_with_context(content, message):
    with pytest.raises(ValueError, match=message) as error:
        tracks_of(content)
    assert "layer 'F.Cu'" in str(error.value) and "net 'PWR'" in str(error.value)


def test_track_under_a_nonzero_location_raises():
    with pytest.raises(ValueError, match="contour or track under a nonzero Location"):
        parse_ipc2581_copper(export(f_cu=f'<Set net="PWR">{special(SEGMENT, location=(1, 0))}</Set>'))


# ---- Pads and vias ------------------------------------------------------------------------
PAD_SHAPES = SHAPES + (
    '<EntryStandard id="CHAM"><RectCham width="2.0" height="1.0" chamfer="0.4" upperLeft="true"/>'
    '</EntryStandard>'
    '<EntryStandard id="RR"><RectRound width="1.2" height="1.4" radius="0.25" upperRight="true" '
    'upperLeft="true" lowerRight="true" lowerLeft="true"/></EntryStandard>'
    '<EntryStandard id="OV"><Oval width="1.7" height="2.0"/></EntryStandard>'
    f'<EntryStandard id="TRI">{contour([(1, 0), (0.5, -0.75), (-0.5, -0.75), (-0.5, 0.75), (0.5, 0.75)])}'
    '</EntryStandard>')


def pad(shape, x, y, rotation=None, pin=("U1", "1")):
    xform = f'<Xform rotation="{rotation}"/>' if rotation is not None else ""
    ref = f'<PinRef componentRef="{pin[0]}" pin="{pin[1]}"/>' if pin else ""
    return (f'<Pad padstackDefRef="P">{xform}<Location x="{x}" y="{y}"/>'
            f'<StandardPrimitiveRef id="{shape}"/>{ref}</Pad>')


def pads_of(content, shapes=PAD_SHAPES):
    return parse_ipc2581_copper(export(f_cu=content, b_cu="", drill="", shapes=shapes)).pads


def test_pads_and_vias_are_read_on_every_layer_with_ids_and_kinds():
    result = parse_ipc2581_copper(export())
    assert [(f.id, f.kind, f.net_id, f.source_ref) for f in result.pads] == [
        ("via:F.Cu:0", "via", "PWR", "LayerFeature[@layerRef='F.Cu']/Set[1]/Pad[1]"),
        ("pad:F.Cu:U1.3", "pad", "PWR", "LayerFeature[@layerRef='F.Cu']/Set[2]/Pad[1]"),
        ("via:B.Cu:0", "via", "PWR", "LayerFeature[@layerRef='B.Cu']/Set[2]/Pad[1]")]
    via = result.pads[0].geometry   # 0.6 mm circle at IPC (25, -2) -> metres, Y reversed
    assert via.centroid.coords[0] == pytest.approx((0.025, 0.002), abs=1e-12)
    # A 0.6 mm circle has 39 vertices at 1 µm, about 0.43% below a true circle.
    assert 0.99 * math.pi * 0.0003 ** 2 < via.area < math.pi * 0.0003 ** 2
    rect = result.pads[1].geometry  # 1.0 x 0.6 mm rectangle at IPC (5, -5)
    assert rect.bounds == pytest.approx((0.0045, 0.0047, 0.0055, 0.0053), abs=1e-12)


def test_rotation_is_counter_clockwise_in_ipc_coordinates_and_y_is_reversed_once():
    # 2 x 1 mm pad, only its upper-left corner chamfered, turned 90 degrees at IPC (10, -20).
    # Counter-clockwise, upper-left (-1, 0.5) goes to (-0.5, -1): IPC (9.5, -21) -> metres
    # (0.0095, 0.021). The opposite end of that side, (1, 0.5) -> (9.5, -19), stays square.
    feature, = pads_of(f'<Set net="PWR">{pad("CHAM", 10, -20, 90)}</Set>')
    assert not feature.geometry.contains(Point(0.00952, 0.02098))   # cut corner
    assert feature.geometry.contains(Point(0.00952, 0.01902))       # square corner
    assert feature.geometry.contains(Point(0.01048, 0.02098)) and feature.geometry.contains(Point(0.01048, 0.01902))
    assert feature.geometry.area == pytest.approx((2.0 - 0.08) * 1e-6, rel=1e-12)


def test_every_dictionary_shape_with_its_size():
    content = ('<Set net="A">' + pad("RR", 0, 0, pin=("R1", "1")) + pad("OV", 10, 0, pin=("J1", "2"))
               + pad("TRI", 20, 0, 20, pin=("JP1", "1")) + pad("C1", 30, 0, pin=("TP1", "1"))
               + pad("R1", 40, 0, pin=("R2", "1")) + "</Set>")
    rr, ov, tri, circle, rect = pads_of(content)
    assert rr.geometry.area == pytest.approx((1.2 * 1.4 - (4 - math.pi) * 0.25 ** 2) * 1e-6, rel=1e-3)
    assert rr.geometry.bounds == pytest.approx((-0.0006, -0.0007, 0.0006, 0.0007), abs=1e-9)
    # The apex of a round end need not be a vertex: within the 1 µm chord rule of the full size.
    assert ov.geometry.bounds == pytest.approx((0.00915, -0.001, 0.01085, 0.001), abs=1e-6)
    assert 0.99 * math.pi * 0.0003 ** 2 < circle.geometry.area < math.pi * 0.0003 ** 2
    assert rect.geometry.area == pytest.approx(0.6e-6, rel=1e-12)
    # Custom outline as given, turned 20 degrees counter-clockwise, then Y reversed: the tip
    # (1, 0) lands at (20 + cos 20, sin 20) in IPC and (.., -sin 20) mm in metres.
    a = math.radians(20)
    tip = ((20 + math.cos(a)) * 1e-3, -math.sin(a) * 1e-3)
    assert any(math.dist(tip, p) < 1e-12 for p in tri.geometry.exterior.coords)
    assert tri.geometry.area == pytest.approx(1.875e-6, rel=1e-12)   # 1 x 1.5 rectangle + triangle 1.5 x 0.5 / 2


def test_unassigned_pads_repeated_pin_numbers_and_nested_via_sets():
    content = (f'<Set>{pad("C1", 1, -1, pin=("TP1", "1"))}</Set>'
               f'<Set net="GND">{pad("R1", 0, 0, pin=("U2", "9"))}{pad("R1", 3, 0, pin=("U2", "9"))}'
               f'<Set net="GND" padUsage="VIA">{pad("C1", 6, 0, pin=None)}</Set></Set>')
    no_net, first, second, via = pads_of(content)
    assert (no_net.id, no_net.net_id) == ("pad:F.Cu:TP1.1", None)
    assert (first.id, second.id) == ("pad:F.Cu:U2.9", "pad:F.Cu:U2.9#2")
    assert (via.id, via.kind, via.net_id) == ("via:F.Cu:0", "via", "GND")


@pytest.mark.parametrize("content,shapes,message", [
    (pad("NOPE", 0, 0), PAD_SHAPES, "pad shape 'NOPE' is not in DictionaryStandard"),
    (pad("R1", 0, 0).replace('<Location', '<Xform rotation="0" mirror="true"/><Location'), PAD_SHAPES,
     "Xform must have attributes"),
    (pad("R1", 0, 0, 30).replace('<Location', '<Xform rotation="30"/><Location'), PAD_SHAPES,
     "more than one Xform"),
    (pad("R1", 0, 0).replace('<Location x="0" y="0"/>', ''), PAD_SHAPES, "Pad has no Location"),
    (pad("R1", 0, 0).replace('<StandardPrimitiveRef id="R1"/>', '<UserPrimitiveRef id="R1"/>'), PAD_SHAPES,
     "UserPrimitiveRef in Pad"),
    (pad("R1", 0, 0).replace('padstackDefRef="P"', 'padstackDefRef="P" layer="x"'), PAD_SHAPES,
     "Pad attributes"),
    (pad("R1", 0, 0, pin=None), PAD_SHAPES, "component pad without a PinRef"),
    (pad("R1", 0, 0).replace('pin="1"', ''), PAD_SHAPES, "PinRef must have attributes"),
    (pad("RR", 0, 0), PAD_SHAPES.replace('lowerLeft="true"/>', 'lowerLeft="false"/>'), "some corners not rounded"),
    (pad("CHAM", 0, 0), PAD_SHAPES.replace('upperLeft="true"/>', 'upperLeft="false"/>'), "must be 'true'"),
    (pad("BF", 0, 0), PAD_SHAPES + '<EntryStandard id="BF"><Butterfly shape="ROUND" diameter="1"/></EntryStandard>',
     "pad shape Butterfly is not supported"),
    (pad("C1", 0, 0), PAD_SHAPES.replace('<Circle diameter="0.6"/>', '<Circle diameter="0.6"><FillDesc fillProperty="HOLLOW"/></Circle>'),
     "no children"),
    (pad("C1", 0, 0), PAD_SHAPES.replace('diameter="0.6"', 'diameter="0"'), "diameter 0.0 must be positive"),
    (pad("RR", 0, 0), PAD_SHAPES.replace('radius="0.25"', 'radius="0.8"'), "corner radius"),
    (pad("TRI", 0, 0), PAD_SHAPES.replace('<PolyStepSegment x="0.5" y="-0.75"/>',
                                          '<PolyStepCurve x="0.5" y="-0.75" centerX="0" centerY="0" clockwise="true"/>'),
     "curved"),
])
def test_unsupported_pads_raise_with_context(content, shapes, message):
    with pytest.raises(ValueError, match=message) as error:
        pads_of(f'<Set net="PWR">{content}</Set>', shapes)
    assert "layer 'F.Cu'" in str(error.value) and "net 'PWR'" in str(error.value)


def test_via_pad_with_pin_raises():
    with pytest.raises(ValueError, match="via pad with a PinRef"):
        pads_of(f'<Set net="PWR" padUsage="VIA">{pad("C1", 0, 0)}</Set>')


@pytest.mark.parametrize("shapes,message", [
    (PAD_SHAPES + '<EntryStandard id="C1"><Circle diameter="1"/></EntryStandard>', "duplicate dictionary id 'C1'"),
    (PAD_SHAPES.replace('<Circle diameter="0.6"/>', '<Circle diameter="0.6"/><Circle diameter="1"/>'),
     "EntryStandard 'C1' must hold exactly one shape"),
])
def test_broken_dictionary_raises_with_its_path(shapes, message):
    with pytest.raises(ValueError, match="DictionaryStandard/EntryStandard.*" + message):
        pads_of(f'<Set net="PWR">{pad("C1", 0, 0)}</Set>', shapes)


def test_custom_pad_keeps_its_handedness():
    # An L is asymmetric in every direction, unlike KiCad's triangle jumper pad, so a mirrored
    # custom outline fails here. Pad frame (IPC, Y up): long leg along +x, short leg up +y.
    ell = contour([(0, 0), (3, 0), (3, 1), (1, 1), (1, 2), (0, 2)])
    shapes = PAD_SHAPES + f'<EntryStandard id="ELL">{ell}</EntryStandard>'
    feature, = pads_of(f'<Set net="PWR">{pad("ELL", 10, -20)}</Set>', shapes)
    # Metres reverse Y once: the long leg runs along +x at y = 0.020 .. 0.019, the short leg
    # points to smaller y (KiCad's "up" on screen).
    assert feature.geometry.contains(Point(0.0125, 0.0195))      # long leg
    assert feature.geometry.contains(Point(0.0105, 0.0185))      # short leg, upwards on screen
    assert not feature.geometry.contains(Point(0.0105, 0.0215))  # where a mirror would put it
    assert not feature.geometry.contains(Point(0.0125, 0.0185))  # the L's empty corner


# ---- Drills -------------------------------------------------------------------------------
def hole(name, x, y, status="PLATED", diameter="1.0", padstack="P", net="PWR"):
    net_attr = f' net="{net}"' if net else ""
    return (f'<Set geometry="{padstack}"{net_attr}><Hole name="{name}" diameter="{diameter}" '
            f'platingStatus="{status}" {TOLS} x="{x}" y="{y}"/></Set>')


def test_drills_in_metres_with_kind_span_and_links_to_via_copper():
    result = parse_ipc2581_copper(export())
    h1, h2 = result.drills
    assert (h1.id, h1.kind, h1.plating, h1.start_layer_id, h1.end_layer_id) == \
        ("drill:H1", "via", "plated", "F.Cu", "B.Cu")
    assert h1.position_m == pytest.approx((0.025, 0.002), abs=1e-15)    # IPC (25, -2), Y reversed
    assert h1.diameter_m == pytest.approx(0.0003, abs=1e-18)
    assert (h2.id, h2.kind, h2.plating, h2.diameter_m) == ("drill:H2", "mechanical", "nonplated", pytest.approx(0.0026))
    assert {f.id: f.drill_id for f in result.pads} == {
        "via:F.Cu:0": "drill:H1", "pad:F.Cu:U1.3": None, "via:B.Cu:0": "drill:H1"}


def test_plated_pin_hole_links_its_pad_on_every_layer_and_nothing_else():
    pin = pad("R1", 5, -5, pin=("J1", "1"))
    other_net = pad("R1", 5, -5, pin=("J1", "2"))
    data = export(f_cu=f'<Set net="PWR">{pin}</Set><Set net="GND">{other_net.replace("5", "8", 1)}</Set>',
                  b_cu=f'<Set net="PWR">{pin}</Set>', drill=hole("H7", 5, -5), shapes=PAD_SHAPES)
    result = parse_ipc2581_copper(data)
    assert {f.id: f.drill_id for f in result.pads} == {
        "pad:F.Cu:J1.1": "drill:H7", "pad:F.Cu:J1.2": None, "pad:B.Cu:J1.1": "drill:H7"}
    assert result.drills[0].kind == "pin"


def test_nonplated_hole_with_or_without_copper():
    ring = f'<Set>{pad("C1", 3, -3, pin=("H1", "1"))}</Set>'
    result = parse_ipc2581_copper(export(f_cu=ring, b_cu="", shapes=PAD_SHAPES,
                                         drill=hole("H9", 3, -3, "NONPLATED", "0.4", net=None)
                                         + hole("H10", 9, -9, "NONPLATED", "3.2", "M", net=None)))
    assert [(d.id, d.kind) for d in result.drills] == [("drill:H9", "mechanical"), ("drill:H10", "mechanical")]
    assert result.pads[0].drill_id == "drill:H9"


def test_blind_via_links_only_the_layers_of_its_span():
    # A 3-copper-layer variant: a via from F.Cu to In1.Cu; B.Cu has via copper at the same
    # spot (another via's), which this hole must not claim.
    layers3 = ('<Layer name="F.Cu" layerFunction="CONDUCTOR"/>'
               '<Layer name="DIELECTRIC_A" layerFunction="DIELPREG"/>'
               '<Layer name="In1.Cu" layerFunction="CONDUCTOR"/>'
               '<Layer name="DIELECTRIC_B" layerFunction="DIELCORE"/>'
               '<Layer name="B.Cu" layerFunction="CONDUCTOR"/>'
               '<Layer name="F.Cu_In1.Cu" layerFunction="DRILL"><Span fromLayer="F.Cu" toLayer="In1.Cu"/></Layer>')
    stack3 = ('<Stackup name="s"><StackupGroup name="g">'
              '<StackupLayer layerOrGroupRef="F.Cu" thickness="0.035" sequence="0"/>'
              '<StackupLayer layerOrGroupRef="DIELECTRIC_A" thickness="0.2" sequence="1"/>'
              '<StackupLayer layerOrGroupRef="In1.Cu" thickness="0.035" sequence="2"/>'
              '<StackupLayer layerOrGroupRef="DIELECTRIC_B" thickness="1.2" sequence="3"/>'
              '<StackupLayer layerOrGroupRef="B.Cu" thickness="0.035" sequence="4"/>'
              '</StackupGroup></Stackup>')
    via = f'<Set net="PWR" padUsage="VIA">{pad("C1", 7, -7, pin=None)}</Set>'
    step = ('<Step name="board">' + layer_feature("F.Cu", via) + layer_feature("In1.Cu", via)
            + layer_feature("B.Cu", via) + layer_feature("F.Cu_In1.Cu", hole("H3", 7, -7, "VIA", "0.2"))
            + '</Step>')
    data = export(step=step, shapes=PAD_SHAPES).replace(LAYERS.encode(), layers3.encode()) \
        .replace(STACKUP.encode(), stack3.encode())
    result = parse_ipc2581_copper(data)
    assert [l.id for l in result.copper_layers] == ["F.Cu", "In1.Cu", "B.Cu"]
    assert (result.drills[0].start_layer_id, result.drills[0].end_layer_id) == ("F.Cu", "In1.Cu")
    assert {f.id: f.drill_id for f in result.pads} == {
        "via:F.Cu:0": "drill:H3", "via:In1.Cu:0": "drill:H3", "via:B.Cu:0": None}


VIA_F = f'<Set net="PWR" padUsage="VIA">{pad("C1", 1, -1, pin=None)}</Set>'
PIN_F = f'<Set net="PWR">{pad("R1", 1, -1, pin=("J1", "1"))}</Set>'


@pytest.mark.parametrize("f_cu,drill,message", [
    (VIA_F, hole("H1", 1, -1, "VIA", padstack="P", net="GND"), "plated hole with no pad or via copper"),
    (VIA_F, hole("H1", 1.001, -1, "VIA"), "plated hole with no pad or via copper"),
    (VIA_F, hole("H1", 1, -1, "VIA", padstack="Q"), "plated hole with no pad or via copper"),
    (VIA_F + VIA_F, hole("H1", 1, -1, "VIA"), "2 pads on 'F.Cu' share this hole"),
    (VIA_F, hole("H1", 1, -1, "VIA") + hole("H2", 1, -1, "VIA"), "already has drill drill:H1"),
    (PIN_F, hole("H1", 1, -1, "VIA"), "VIA hole at pad"),
    (VIA_F, hole("H1", 1, -1, "PLATED"), "PLATED hole at via"),
    (VIA_F, hole("H1", 1, -1, "VIA").replace('platingStatus="VIA"', 'platingStatus="SEMIPLATED"'), "SEMIPLATED"),
    (VIA_F, hole("H1", 1, -1, "VIA").replace('plusTol="0.0"', 'plusTol="0.05"'), "nonzero plusTol"),
    (VIA_F, hole("H1", 1, -1, "VIA").replace(' minusTol="0.0"', ''), "Hole must have attributes"),
    (VIA_F, hole("H1", 1, -1, "VIA").replace('</Set>', '<Hole name="H2" diameter="1" platingStatus="VIA" '
                                             f'{TOLS} x="1" y="-1"/></Set>'), "exactly one Hole"),
    (VIA_F, hole("H1", 1, -1, "VIA").replace('<Hole', '<SlotCavity name="S" platingStatus="PLATED"/><Hole'),
     "slots and backdrills are not supported"),
    (VIA_F, hole("H1", 1, -1, "VIA").replace('geometry="P"', 'geometry="P" polarity="NEGATIVE"'),
     "Set attributes"),
    (VIA_F, '<Hole name="H1"/>', "Hole in a drill LayerFeature"),
])
def test_unsupported_or_unmatched_drills_raise(f_cu, drill, message):
    with pytest.raises(ValueError, match=message) as error:
        parse_ipc2581_copper(export(f_cu=f_cu, b_cu="", drill=drill, shapes=PAD_SHAPES))
    assert "F.Cu_B.Cu" in str(error.value)


@pytest.mark.parametrize("old,new,message", [
    ('<Span fromLayer="F.Cu" toLayer="B.Cu"/>', '', "one Span"),
    ('<Span fromLayer="F.Cu" toLayer="B.Cu"/>', '<Span fromLayer="B.Cu" toLayer="F.Cu"/>', "not an ordered pair"),
    ('<Span fromLayer="F.Cu" toLayer="B.Cu"/>', '<Span fromLayer="F.Mask" toLayer="B.Cu"/>', "not an ordered pair"),
])
def test_bad_drill_spans_raise(old, new, message):
    with pytest.raises(ValueError, match=message):
        parse_ipc2581_copper(export().replace(old.encode(), new.encode()))


def test_routed_slot_layer_with_content_raises():
    # KiCad writes oblong holes to a layerFunction="ROUT" layer as SlotCavity elements.
    slot_layer = ('<Layer name="F.Cu_B.Cu_SLOT" layerFunction="ROUT">'
                  '<Span fromLayer="F.Cu" toLayer="B.Cu"/></Layer>')
    data = export(step_extra=layer_feature("F.Cu_B.Cu_SLOT", '<Set><SlotCavity name="S1"/></Set>'))
    with pytest.raises(ValueError, match="routed slots are not supported"):
        parse_ipc2581_copper(data.replace(LAYERS.encode(), (LAYERS + slot_layer).encode()))

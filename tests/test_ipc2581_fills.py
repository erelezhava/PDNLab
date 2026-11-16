"""IPC-2581 nets and zone-fill reader: hand-written XML only, no real or private boards.

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

import pytest
from shapely.geometry import Polygon

from pdnlab_dc.board import BoardModel
from pdnlab_dc.ipc2581_fills import (SCOPE, Ipc2581ZoneFills, UnreadCount,
                                     parse_ipc2581_fills, read_ipc2581_fills)
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
DRILL = ('<Set geometry="V" net="SIG"><Hole name="H1" diameter="0.3" platingStatus="VIA" x="25" y="-2"/></Set>')


def layer_feature(layer, content):
    return f'<LayerFeature layerRef="{layer}">{content}</LayerFeature>'


def export(f_cu=F_CU, b_cu=B_CU, drill=DRILL, *, step=None, step_extra="", revision="C"):
    if step is None:
        step = (f'<Step name="board"><Datum x="0.0" y="0.0"/>{step_extra}'
                f'{layer_feature("F.Cu", f_cu)}{layer_feature("B.Cu", b_cu)}'
                f'{layer_feature("F.Cu_B.Cu", drill)}</Step>')
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<IPC-2581 revision="{revision}" '
            f'xmlns="{NAMESPACE}"><Content><DictionaryStandard units="MILLIMETER"/></Content>'
            f'<Ecad name="Design"><CadHeader units="MILLIMETER"/>'
            f'<CadData>{LAYERS}{STACKUP}{step}</CadData></Ecad></IPC-2581>').encode()


def by_id(result):
    return {f.id: f for f in result.fills}


def test_fill_coordinates_are_reversed_in_y_once_and_scaled_to_metres_once():
    fill = by_id(parse_ipc2581_fills(export()))["fill:F.Cu:0"]
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
    fill = by_id(parse_ipc2581_fills(export()))["fill:B.Cu:0"]
    assert fill.net_id == "GND" and len(fill.geometry.interiors) == 1
    assert fill.geometry.area == pytest.approx(196e-6, rel=1e-12)
    assert {tuple(round(v, 12) for v in p) for p in fill.geometry.interiors[0].coords} == \
        {(0.004, 0.004), (0.006, 0.004), (0.006, 0.006), (0.004, 0.006)}


def test_kicad_fractured_fill_is_split_into_outline_and_hole_without_moving_vertices():
    raw = Polygon([(x, y) for x, y in FRACTURED])
    assert not raw.is_valid  # why the slit form needs handling: Shapely rejects it as given
    fill = by_id(parse_ipc2581_fills(export()))["fill:B.Cu:1"]
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
        fill = parse_ipc2581_fills(export(b_cu=f'<Set net="GND">{special(contour(rotated))}</Set>')).fills[-1]
        assert len(fill.geometry.interiors) == 2
        assert fill.geometry.area == pytest.approx((300 - 4 - 2) * 1e-6, rel=1e-12)
        assert fill.geometry.bounds == pytest.approx((0, 0, 0.030, 0.010), abs=1e-15)


def test_nets_come_from_every_set_and_unassigned_copper_has_no_net():
    result = parse_ipc2581_fills(export())
    # SIG appears only on the drill layer; no net is invented for the unassigned fill.
    assert [(n.id, n.name) for n in result.nets] == [("GND", "GND"), ("PWR", "PWR"), ("SIG", "SIG")]
    assert [(f.id, f.layer_id, f.net_id) for f in result.fills] == [
        ("fill:F.Cu:0", "F.Cu", "PWR"), ("fill:F.Cu:1", "F.Cu", None),
        ("fill:B.Cu:0", "B.Cu", "GND"), ("fill:B.Cu:1", "B.Cu", "GND")]


def test_feature_ids_and_source_references_are_stable_and_traceable():
    first, second = (parse_ipc2581_fills(export()) for _ in range(2))
    assert [(f.id, f.source_ref) for f in first.fills] == [(f.id, f.source_ref) for f in second.fills]
    assert [f.source_ref for f in first.fills] == [
        "LayerFeature[@layerRef='F.Cu']/Set[1]/Features[1]/UserSpecial[1]/Contour[1]",
        "LayerFeature[@layerRef='F.Cu']/Set[3]/Features[1]/UserSpecial[1]/Contour[1]",
        "LayerFeature[@layerRef='B.Cu']/Set[1]/Features[1]/UserSpecial[1]/Contour[1]",
        "LayerFeature[@layerRef='B.Cu']/Set[1]/Features[1]/UserSpecial[1]/Contour[2]"]


def test_unread_copper_is_counted_not_read_and_other_layers_are_listed():
    result = parse_ipc2581_fills(export())
    assert result.unread == (
        UnreadCount("pad", "F.Cu", "PWR", 1),
        UnreadCount("track or graphic arc", "F.Cu", "PWR", 1),
        UnreadCount("track or graphic segment", "F.Cu", "PWR", 1),
        UnreadCount("via pad", "F.Cu", "PWR", 1),
        UnreadCount("track or graphic segment", "B.Cu", "PWR", 1),
        UnreadCount("via pad", "B.Cu", "PWR", 1))
    assert result.uninspected_layers == ("F.Cu_B.Cu",)
    assert len(result.fills) == 4


@pytest.mark.parametrize("extra,message", GRAPHIC_SETS)
def test_copper_no_increment_reads_raises_instead_of_being_counted(extra, message):
    # Appended after the readable Sets, so it is Set[4] on F.Cu.
    with pytest.raises(ValueError, match=message) as error:
        parse_ipc2581_fills(export(f_cu=F_CU + extra))
    assert "layer 'F.Cu'" in str(error.value) and "Set[4]" in str(error.value)


def test_result_is_scoped_and_not_a_board_model():
    data = export()
    result = parse_ipc2581_fills(data)
    meta = parse_ipc2581_metadata(data)
    assert isinstance(result, Ipc2581ZoneFills) and not isinstance(result, BoardModel)
    assert {f.name for f in fields(result)} == {"source", "copper_layers", "nets", "fills", "unread",
                                                "uninspected_layers", "scope"}
    assert result.scope == SCOPE and "not read" in SCOPE and "not a complete board" in SCOPE
    assert result.source.assumptions[0] == SCOPE
    assert any("applied once" in a for a in result.source.assumptions)
    assert not any("has not been applied" in a for a in result.source.assumptions)
    # Header, units, layers and provenance come from the metadata reader unchanged.
    assert result.copper_layers == meta.copper_layers
    assert result.source.input_sha256 == meta.source.input_sha256 == hashlib.sha256(data).hexdigest()
    assert result.source.xy_transform == XY_TRANSFORM
    assert result.source.assumptions[-2:] == meta.source.assumptions[-2:]


def test_copper_layer_without_features_and_board_without_fills():
    result = parse_ipc2581_fills(export(f_cu=f'<Set net="PWR">{PIN_PAD}</Set>', b_cu=""))
    assert result.fills == () and [n.id for n in result.nets] == ["PWR", "SIG"]
    step = '<Step name="board"></Step>'
    result = parse_ipc2581_fills(export(step=step))
    assert (result.nets, result.fills, result.unread, result.uninspected_layers) == ((), (), (), ())


def test_read_from_path_hashes_the_bytes_parsed(tmp_path):
    path = tmp_path / "board.xml"
    path.write_bytes(export())
    assert read_ipc2581_fills(path) == parse_ipc2581_fills(export())


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
        parse_ipc2581_fills(data)


@pytest.mark.parametrize("content,net", [
    (f'<Set net="PWR">{special(contour(SQUARE), location=(2, 0))}</Set>', "net 'PWR'"),
    (f'<Set>{special(contour(BOWTIE))}</Set>', "no net"),
])
def test_errors_name_layer_net_and_element(content, net):
    with pytest.raises(ValueError) as error:
        parse_ipc2581_fills(export(f_cu=content))
    assert "layer 'F.Cu'" in str(error.value) and net in str(error.value)
    assert "LayerFeature[@layerRef='F.Cu']/Set[1]/Features[1]" in str(error.value)


def test_unread_content_is_not_validated_but_is_counted():
    # The track increment will validate Lines; here an odd Line is only counted.
    odd = '<Line startX="0" startY="0" endX="1" endY="0"><LineDesc lineWidth="0.5" lineEnd="SQUARE"/></Line>'
    result = parse_ipc2581_fills(fill_on_f_cu(special(odd, odd)))
    assert UnreadCount("track or graphic segment", "F.Cu", "PWR", 2) in result.unread


def test_footprint_set_without_copper_is_accepted_and_nothing_counted():
    result = parse_ipc2581_fills(export(f_cu='<Set componentRef="U1"><Features><Location x="0" y="0"/>'
                                             '<UserSpecial/></Features></Set>'))
    assert not [u for u in result.unread if u.layer_id == "F.Cu"]
    with pytest.raises(ValueError, match="component Set content"):
        parse_ipc2581_fills(export(f_cu=f'<Set componentRef="U1">{PIN_PAD}</Set>'))

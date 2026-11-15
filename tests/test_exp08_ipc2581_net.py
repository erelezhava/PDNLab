"""The IPC-2581 reader must describe a board exactly as the direct .kicad_pcb reader does.

Both inputs below are written by hand for the same small board: a fill and a rotated
rounded-rectangle SMD pad on F.Cu, a track and a rectangular SMD pad on B.Cu, one through
via and one through-hole pad. The export uses the element layout of KiCad 10's IPC-2581 C
output (Y axis reversed, via pads without a PinRef, holes on a drill layer with a Span).
"""

import importlib.util
from pathlib import Path
import shutil

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "experiments" / "exp08_ipc2581_net.py"
spec = importlib.util.spec_from_file_location("exp08_ipc2581_net", SCRIPT)
ipc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipc)
exp08 = ipc.exp08

BOARD = '''(kicad_pcb
 (layers (0 "F.Cu" signal) (2 "B.Cu" signal))
 (setup (stackup
  (layer "F.Cu" (type "copper") (thickness 0.035))
  (layer "dielectric 1" (type "core") (thickness 1.53))
  (layer "B.Cu" (type "copper") (thickness 0.035))))
 (footprint "a" (layer "F.Cu") (at 2 5) (property "Reference" "P1")
  (pad "1" smd roundrect (at 0 0 90) (size 2 4) (layers "F.Cu") (roundrect_rratio 0.25) (net "PWR") (pintype "passive")))
 (footprint "b" (layer "B.Cu") (at 18 5) (property "Reference" "P2")
  (pad "1" smd rect (at 0 0) (size 2 4) (layers "B.Cu") (net "PWR") (pintype "power_in")))
 (footprint "c" (layer "F.Cu") (at 10 8) (property "Reference" "J1")
  (pad "1" thru_hole circle (at 0 0) (size 1.7 1.7) (drill 1) (layers "*.Cu") (net "PWR") (pintype "passive")))
 (segment (start 12 5) (end 18 5) (width 0.5) (layer "B.Cu") (net "PWR"))
 (via (at 12 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "PWR"))
 (zone (net "PWR") (layer "F.Cu") (fill yes (thermal_gap 0.5))
  (filled_polygon (layer "F.Cu") (pts (xy 0 0) (xy 14 0) (xy 14 10) (xy 0 10)))))'''

EXPORT = '''<?xml version="1.0" encoding="utf-8"?>
<IPC-2581 revision="C" xmlns="http://webstds.ipc.org/2581">
 <Content>
  <DictionaryStandard units="MILLIMETER">
   <EntryStandard id="ROUNDRECT_1"><RectRound width="2.0" height="4.0" radius="0.50" upperRight="true" upperLeft="true" lowerRight="true" lowerLeft="true"/></EntryStandard>
   <EntryStandard id="RECT_2"><RectCenter width="2.0" height="4.0"/></EntryStandard>
   <EntryStandard id="CIRCLE_3"><Circle diameter="1.70"/></EntryStandard>
   <EntryStandard id="CIRCLE_4"><Circle diameter="0.60"/></EntryStandard>
  </DictionaryStandard>
 </Content>
 <Ecad>
  <CadHeader units="MILLIMETER"/>
  <CadData>
   <Layer name="F.Mask" layerFunction="SOLDERMASK"/>
   <Layer name="F.Cu" layerFunction="CONDUCTOR"/>
   <Layer name="DIELECTRIC_1" layerFunction="DIELCORE"/>
   <Layer name="B.Cu" layerFunction="CONDUCTOR"/>
   <Layer name="F.Cu_B.Cu" layerFunction="DRILL"><Span fromLayer="F.Cu" toLayer="B.Cu"/></Layer>
   <Stackup><StackupGroup>
    <StackupLayer layerOrGroupRef="F.Mask" thickness="0.010" sequence="0"/>
    <StackupLayer layerOrGroupRef="F.Cu" thickness="0.0350" sequence="1"/>
    <StackupLayer layerOrGroupRef="DIELECTRIC_1" thickness="1.530" sequence="2"/>
    <StackupLayer layerOrGroupRef="B.Cu" thickness="0.0350" sequence="3"/>
   </StackupGroup></Stackup>
   <Step name="board">
    <LayerFeature layerRef="F.Cu">
     <Set net="PWR" padUsage="VIA">
      <Features><UserSpecial><Contour><Polygon>
       <PolyBegin x="0.0" y="0.0"/><PolyStepSegment x="14.0" y="0.0"/><PolyStepSegment x="14.0" y="-10.0"/>
       <PolyStepSegment x="0.0" y="-10.0"/><PolyStepSegment x="0.0" y="0.0"/>
      </Polygon></Contour></UserSpecial></Features>
      <Pad padstackDefRef="V"><Location x="12.0" y="-5.0"/><StandardPrimitiveRef id="CIRCLE_4"/></Pad>
     </Set>
     <Set net="PWR">
      <Pad padstackDefRef="A"><Xform rotation="90.0"/><Location x="2.0" y="-5.0"/><StandardPrimitiveRef id="ROUNDRECT_1"/><PinRef componentRef="P1" pin="1"/></Pad>
      <Pad padstackDefRef="C"><Location x="10.0" y="-8.0"/><StandardPrimitiveRef id="CIRCLE_3"/><PinRef componentRef="J1" pin="1"/></Pad>
     </Set>
     <Set net="OTHER"><Pad padstackDefRef="A"><Location x="30.0" y="-5.0"/><StandardPrimitiveRef id="RECT_2"/><PinRef componentRef="P9" pin="1"/></Pad></Set>
    </LayerFeature>
    <LayerFeature layerRef="B.Cu">
     <Set net="PWR" padUsage="VIA">
      <Features><UserSpecial><Line startX="12.0" startY="-5.0" endX="18.0" endY="-5.0"><LineDesc lineWidth="0.50" lineEnd="ROUND"/></Line></UserSpecial></Features>
      <Pad padstackDefRef="V"><Location x="12.0" y="-5.0"/><StandardPrimitiveRef id="CIRCLE_4"/></Pad>
     </Set>
     <Set net="PWR">
      <Pad padstackDefRef="B"><Location x="18.0" y="-5.0"/><StandardPrimitiveRef id="RECT_2"/><PinRef componentRef="P2" pin="1"/></Pad>
      <Pad padstackDefRef="C"><Location x="10.0" y="-8.0"/><StandardPrimitiveRef id="CIRCLE_3"/><PinRef componentRef="J1" pin="1"/></Pad>
     </Set>
    </LayerFeature>
    <LayerFeature layerRef="F.Cu_B.Cu">
     <Set geometry="V" net="PWR"><Hole name="H1" diameter="0.30" platingStatus="VIA" x="12.0" y="-5.0"/></Set>
     <Set geometry="C" net="PWR"><Hole name="H2" diameter="1.0" platingStatus="PLATED" x="10.0" y="-8.0"/></Set>
    </LayerFeature>
   </Step>
  </CadData>
 </Ecad>
</IPC-2581>'''


def write(tmp_path, board=BOARD, export=EXPORT):
    (tmp_path / "b.kicad_pcb").write_text(board, encoding="utf-8")
    (tmp_path / "b.xml").write_text(export, encoding="utf-8")
    return (exp08.read_net(str(tmp_path / "b.kicad_pcb"), "PWR"), ipc.read_net_ipc2581(tmp_path / "b.xml", "PWR"))


def test_export_reader_matches_the_direct_reader(tmp_path):
    direct, export = write(tmp_path)
    report = ipc.compare_geometry(direct, export)
    assert report["equal"], report
    assert report["tracks"] == dict(direct=1, export=1, equal=True)
    assert report["vias"] == dict(direct=1, export=1, equal=True)
    assert report["pads"]["direct"] == report["pads"]["export"] == 3
    assert report["zones"]["F.Cu"]["difference_mm2"] == 0.0 and report["zones"]["F.Cu"]["area_mm2"] == pytest.approx(140.0)
    assert export["copper"] == ["F.Cu", "B.Cu"] and export["centres"] == direct["centres"]
    pads = {p["ref"]: p for p in export["pads"]}
    assert pads["J1"]["kind"] == "thru_hole" and pads["J1"]["drill"] == 1.0 and pads["J1"]["layers"] == ["F.Cu", "B.Cu"]
    assert pads["P1"]["kind"] == "smd" and pads["P1"]["drill"] is None and pads["P1"]["layers"] == ["F.Cu"]
    # The 2 x 4 mm pad is turned by 90 degrees: 4 mm along x.
    x0, y0, x1, y1 = pads["P1"]["poly"].bounds
    assert (x1 - x0, y1 - y0) == pytest.approx((4.0, 2.0))


def test_what_the_export_does_not_carry_is_empty_not_guessed(tmp_path):
    direct, export = write(tmp_path)
    assert direct["relief"] == {"F.Cu": 0.5} and export["relief"] == {}
    assert {p["type"] for p in direct["pads"]} == {"passive", "power_in"}
    assert {p["type"] for p in export["pads"]} == {""}


@pytest.mark.parametrize("old,new,field", [
    ('<Hole name="H1" diameter="0.30"', '<Hole name="H1" diameter="0.35"', "vias"),
    ('<LineDesc lineWidth="0.50"', '<LineDesc lineWidth="0.40"', "tracks"),
])
def test_comparison_sees_a_changed_via_or_track(tmp_path, old, new, field):
    assert old in EXPORT
    direct, export = write(tmp_path, export=EXPORT.replace(old, new))
    report = ipc.compare_geometry(direct, export)
    assert not report["equal"] and not report[field]["equal"]


def test_comparison_sees_a_moved_fill_corner_and_a_resized_pad(tmp_path):
    direct, export = write(tmp_path, export=EXPORT.replace('<PolyStepSegment x="14.0" y="0.0"/>', '<PolyStepSegment x="14.5" y="0.0"/>'))
    report = ipc.compare_geometry(direct, export)
    assert not report["equal"] and report["zones"]["F.Cu"]["difference_mm2"] == pytest.approx(2.5)
    direct, export = write(tmp_path, export=EXPORT.replace('<RectCenter width="2.0"', '<RectCenter width="2.2"'))
    report = ipc.compare_geometry(direct, export)
    assert not report["equal"] and report["pads"]["max_outline_shift_mm"] > 0.01


@pytest.mark.parametrize("old,new,message", [
    ('<Xform rotation="90.0"/>', '<Xform rotation="45.0"/>', "multiple of 90"),
    ('<Xform rotation="90.0"/>', '<Xform rotation="90.0" mirror="true"/>', "mirrored"),
    ('<StandardPrimitiveRef id="RECT_2"/><PinRef componentRef="P2"', '<UserPrimitiveRef id="U1"/><PinRef componentRef="P2"', "custom pad"),
    ('<PolyStepSegment x="14.0" y="0.0"/>', '<PolyStepCurve x="14.0" y="0.0" centerX="7" centerY="0" clockwise="true"/>', "PolyStepCurve"),
    ('<Line startX="12.0"', '<Arc startX="12.0"', "arcs"),
    ('revision="C"', 'revision="B"', "revision C"),
    ('<Span fromLayer="F.Cu" toLayer="B.Cu"/>', '<Span fromLayer="F.Cu" toLayer="F.Cu"/>', "blind and buried"),
])
def test_unsupported_export_content_is_an_error(tmp_path, old, new, message):
    assert old in EXPORT
    (tmp_path / "b.xml").write_text(EXPORT.replace(old, new).replace("</Line>", "</Arc>" if "Arc" in new else "</Line>"),
                                    encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        ipc.read_net_ipc2581(tmp_path / "b.xml", "PWR")


def test_cut_outs_in_a_fill_contour_are_read(tmp_path):
    cut = ('</Polygon><Cutout><PolyBegin x="4.0" y="-4.0"/><PolyStepSegment x="6.0" y="-4.0"/>'
           '<PolyStepSegment x="6.0" y="-6.0"/><PolyStepSegment x="4.0" y="-6.0"/><PolyStepSegment x="4.0" y="-4.0"/></Cutout></Contour>')
    (tmp_path / "b.xml").write_text(EXPORT.replace("</Polygon></Contour>", cut), encoding="utf-8")
    export = ipc.read_net_ipc2581(tmp_path / "b.xml", "PWR")
    assert export["zones"]["F.Cu"][0].area == pytest.approx(140.0 - 4.0)


@pytest.mark.skipif(shutil.which("gmsh") is None, reason="gmsh executable required")
def test_both_readers_give_the_same_solve(tmp_path):
    direct, export = write(tmp_path)
    results = []
    for name, board in [("direct", direct), ("export", dict(export, relief=direct["relief"]))]:
        (tmp_path / name).mkdir()
        solved = ipc.solve(board, {"S": ["P1.1"]}, ["P2.1"], tmp_path / name, 0.5, 48, 0.2, 1.724e-8, 0.025, "gmsh")
        assert solved["sources"]["S"]["unreachable"] == []
        results.append(solved["sources"]["S"]["Z"][0, 0])
    # P1 to P2 runs through the F.Cu fill, the via barrel and the B.Cu track: a few milliohms.
    assert 1e-3 < results[0] < 2e-2
    assert results[1] == pytest.approx(results[0], rel=1e-9)


@pytest.mark.parametrize('old,new,message', [
    ('<LineDesc lineWidth="0.50" lineEnd="ROUND"/>', '<LineDesc lineWidth="0.50" lineEnd="SQUARE"/>', 'track ends'),
    ('<Features><UserSpecial>', '<Features><Xform rotation="90"/><UserSpecial>', 'Xform'),
    ('<Line startX="12.0"', '<Polyline startX="12.0"', 'Polyline'),
    ('platingStatus="PLATED"', 'platingStatus="NONPLATED"', 'plating status'),
    ('<Hole name="H2"', '<SlotCavity name="H2"', 'SlotCavity'),
    ('<Xform rotation="90.0"/>', '<Xform rotation="90.0" scale="2"/>', 'transform'),
    ('<Hole name="H2" diameter="1.0" platingStatus="PLATED" x="10.0"',
     '<Hole name="H2" diameter="1.0" platingStatus="PLATED" x="10.1"', 'matching pin pad'),
    ('<Circle diameter="0.60"/>', '<Circle diameter="0.70"/>', None),
])
def test_review_mutations_are_rejected_or_detected(tmp_path, old, new, message):
    xml = EXPORT.replace(old, new)
    if 'Polyline' in new:
        xml = xml.replace('</Line>', '</Polyline>')
    if message:
        (tmp_path / 'b.xml').write_text(xml)
        with pytest.raises(ValueError, match=message):
            ipc.read_net_ipc2581(tmp_path / 'b.xml', 'PWR')
    else:
        direct, export = write(tmp_path, export=xml)
        assert not ipc.compare_geometry(direct, export)['vias']['equal']


@pytest.mark.parametrize('mutation,message', [
    ('missing_via_pad', 'lacks pad copper'),
    ('different_via_size', 'pad sizes'),
    ('orphan_via_pad', 'no matching hole'),
    ('missing_pin_hole', 'no matching plated hole'),
])
def test_holes_and_pads_must_correspond(tmp_path, mutation, message):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(EXPORT)
    if mutation in {'missing_via_pad', 'different_via_size'}:
        group = root.find('.//i:LayerFeature[@layerRef="B.Cu"]/i:Set', ipc.NS)
        pad = group.find('i:Pad', ipc.NS)
        if mutation == 'missing_via_pad':
            group.remove(pad)
        else:
            pad.find('i:StandardPrimitiveRef', ipc.NS).set('id', 'CIRCLE_3')
    else:
        name = 'H1' if mutation == 'orphan_via_pad' else 'H2'
        for group in root.findall('.//i:Set', ipc.NS):
            for hole in group.findall('i:Hole', ipc.NS):
                if hole.get('name') == name:
                    group.remove(hole)
    ET.ElementTree(root).write(tmp_path / 'b.xml')
    with pytest.raises(ValueError, match=message):
        ipc.read_net_ipc2581(tmp_path / 'b.xml', 'PWR')


@pytest.mark.parametrize('rotation', [0, 90, 180, 270, -90, 360])
def test_supported_pad_rotations_have_expected_extent(rotation):
    import xml.etree.ElementTree as ET
    shape = ET.fromstring('<RectCenter width="2" height="4"/>')
    p = ipc._primitive_polygon(shape, (3, 7), rotation)
    expected = (2, 4) if rotation % 180 == 0 else (4, 2)
    x0, y0, x1, y1 = p.bounds
    assert (x1-x0, y1-y0) == pytest.approx(expected)
    assert (p.centroid.x, p.centroid.y) == pytest.approx((3, 7))


def test_y_flip_applies_to_all_geometry(tmp_path):
    _, board = write(tmp_path)
    assert board['tracks'][0][1:3] == ((12, 5), (18, 5))
    assert board['vias'][0]['c'] == (12, 5)
    assert {p['ref']: p['centre'] for p in board['pads']} == {'P1': (2, 5), 'P2': (18, 5), 'J1': (10, 8)}
    assert board['zones']['F.Cu'][0].bounds == (0, 0, 14, 10)


def test_local_pad_outline_error_cannot_hide_in_average_area(tmp_path):
    from shapely.geometry import Polygon
    direct, export = write(tmp_path)
    pad = next(p for p in export['pads'] if p['ref'] == 'P2')
    pad['poly'] = pad['poly'].union(Polygon([(19, 4), (19.0001, 4.0005), (19, 4.001)]))
    report = ipc.compare_geometry(direct, export)
    assert report['pads']['max_outline_shift_mm'] < ipc.RESOLUTION_MM
    assert report['pads']['max_outline_distance_mm'] > ipc.RESOLUTION_MM
    assert not report['equal']

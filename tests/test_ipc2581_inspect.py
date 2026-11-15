"""Audit must compare values and geometry, rather than merely count XML tags."""

import pytest

from pdnlab_dc.ipc2581_inspect import inspect_export, parse_sexpr


# Minimal structural fixtures for the audit, not fabrication-ready boards.
SOURCE = '''(kicad_pcb
 (setup (stackup
  (layer "F.Cu" (thickness 0.035))
  (layer "dielectric 1" (thickness 1.53))
  (layer "B.Cu" (thickness 0.035))))
 (net 0 "") (net 1 "PWR")
 (footprint "test" (property "Reference" "J1")
  (pad "1" smd rect (net 1 "PWR")))
 (zone (net 1) (layer "F.Cu")
  (filled_polygon (layer "F.Cu") (pts (xy 1 2) (xy 4 2) (xy 4 5) (xy 1 5))))
 (via (at 2 3) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1)))'''

XML = '''<IPC-2581 xmlns="http://webstds.ipc.org/2581" revision="C">
 <Ecad><CadHeader units="MILLIMETER"/><CadData>
  <Layer name="F.Cu" layerFunction="CONDUCTOR"/>
  <Layer name="B.Cu" layerFunction="CONDUCTOR"/>
  <Layer name="DRILL" layerFunction="DRILL"><Span fromLayer="F.Cu" toLayer="B.Cu"/></Layer>
  <Stackup><StackupGroup>
   <StackupLayer layerOrGroupRef="F.Cu" thickness="0.035"/>
   <StackupLayer layerOrGroupRef="DIELECTRIC_1" thickness="1.53"/>
   <StackupLayer layerOrGroupRef="B.Cu" thickness="0.035"/>
  </StackupGroup></Stackup>
  <Step><LayerFeature layerRef="F.Cu"><Set net="PWR">
   <Pad><PinRef componentRef="J1" pin="1"/></Pad>
   <Features><UserSpecial><Contour><Polygon>
    <PolyBegin x="4" y="-5"/><PolyStepSegment x="4" y="-2"/>
    <PolyStepSegment x="1" y="-2"/><PolyStepSegment x="1" y="-5"/>
    <PolyStepSegment x="4" y="-5"/>
   </Polygon></Contour></UserSpecial></Features>
  </Set></LayerFeature>
  <LayerFeature layerRef="DRILL"><Set net="PWR">
   <Hole diameter="0.3" platingStatus="VIA" x="2" y="-3"/>
  </Set></LayerFeature></Step>
 </CadData></Ecad></IPC-2581>'''


def audit(tmp_path, xml=XML, source=SOURCE):
    board, export = tmp_path / "board.kicad_pcb", tmp_path / "board.xml"
    board.write_text(source)
    export.write_text(xml)
    return inspect_export(board, export)


def test_exact_values_and_rotated_reversed_polygon_match(tmp_path):
    report = audit(tmp_path)
    assert report["passed"]
    assert report["matched_fill_polygons"] == 1
    assert report["matched_pin_net_mappings"] == 1
    assert report["matched_vias"] == 1
    assert report["exported_stackup_thickness_mm"]["F.Cu"] == 0.035
    assert report["exported_via_diameters_mm"] == [0.3]


@pytest.mark.parametrize("old,new,gate", [
    ('x="1" y="-2"', 'x="1.1" y="-2"', "zone_fills"),
    ('thickness="0.035"', 'thickness="0.018"', "stackup"),
    ('thickness="1.53"', 'thickness="1.6"', "stackup"),
    ('componentRef="J1"', 'componentRef="J2"', "pin_to_net"),
    ('pin="1"', 'pin="2"', "pin_to_net"),
    ('diameter="0.3"', 'diameter="0.4"', "via_holes"),
    ('x="2" y="-3"', 'x="2.1" y="-3"', "via_holes"),
    ('toLayer="B.Cu"', 'toLayer="In1.Cu"', "via_holes"),
])
def test_changed_values_fail_despite_unchanged_counts(tmp_path, old, new, gate):
    report = audit(tmp_path, XML.replace(old, new))
    assert not report["gates"][gate]
    assert not report["passed"]


def test_missing_saved_fill_cannot_pass(tmp_path):
    start = SOURCE.index(' (zone ')
    end = SOURCE.index(' (via ')
    report = audit(tmp_path, source=SOURCE[:start] + SOURCE[end:])
    assert not report["gates"]["zone_fills"]


def test_wrong_net_fails_mapping_and_geometry(tmp_path):
    report = audit(tmp_path, XML.replace('net="PWR"', 'net="GND"'))
    assert not report["gates"]["pin_to_net"]
    assert not report["gates"]["zone_fills"]
    assert not report["gates"]["via_holes"]


@pytest.mark.parametrize("xml", [XML.replace('units="MILLIMETER"', 'units="INCH"'),
                                  XML.replace('revision="C"', 'revision="B"'),
                                  XML.replace('<Features>', '<Features><Xform rotation="90"/>'),
                                  XML.replace('<Features>', '<Features><Location x="1" y="0"/>')])
def test_unsupported_export_rejected(tmp_path, xml):
    with pytest.raises(ValueError):
        audit(tmp_path, xml)


@pytest.mark.parametrize("text", ['(a', '(a))', 'a', '(a) (b)'])
def test_malformed_native_syntax_rejected(text):
    with pytest.raises(ValueError):
        parse_sexpr(text)


def test_escaped_strings():
    assert parse_sexpr(r'(property "Reference" "a\"b\\c")')[2] == 'a"b\\c'

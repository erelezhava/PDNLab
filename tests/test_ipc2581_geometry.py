"""Track, pad and pad-hole audit must compare values, not just count elements."""

from pathlib import Path

import pytest

from pdnlab_dc.ipc2581_geometry import inspect_geometry

# Minimal structural fixtures, not fabrication-ready boards. Footprint R1 is rotated 90
# degrees, so pad 1 at local (1, 0) lands at (10, 19) and pad 2 at (-1, 0) at (10, 21).
SOURCE = '''(kicad_pcb
 (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (1 "F.Mask" user))
 (footprint "t" (at 10 20 90) (layer "F.Cu") (property "Reference" "R1")
  (pad "1" smd rect (at 1 0 90) (size 2 1) (layers "F.Cu" "F.Mask") (net "PWR"))
  (pad "2" thru_hole circle (at -1 0) (size 1.5 1.5) (drill 0.8) (layers "*.Cu") (net "PWR"))
  (pad "3" np_thru_hole circle (at 0 3) (size 2 2) (drill 2) (layers "*.Cu")))
 (segment (start 5 0) (end 0 0) (width 0.25) (layer "F.Cu") (net "PWR"))
 (via (at 3 4) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "PWR")))'''

XML = '''<IPC-2581 xmlns="http://webstds.ipc.org/2581" revision="C">
 <Ecad><CadHeader units="MILLIMETER"/><CadData>
  <Layer name="F.Cu" layerFunction="CONDUCTOR"/>
  <Layer name="B.Cu" layerFunction="CONDUCTOR"/>
  <Layer name="DRILL" layerFunction="DRILL"/>
 </CadData></Ecad>
 <Content><DictionaryStandard units="MILLIMETER">
  <EntryStandard id="RECT_1"><RectCenter width="2" height="1"/></EntryStandard>
  <EntryStandard id="CIRCLE_1"><Circle diameter="1.5"/></EntryStandard>
  <EntryStandard id="CIRCLE_2"><Circle diameter="0.6"/></EntryStandard>
 </DictionaryStandard></Content>
 <Ecad><CadData><Step>
  <LayerFeature layerRef="F.Cu"><Set net="PWR">
   <Features><UserSpecial>
    <Line startX="0" startY="0" endX="5" endY="0"><LineDesc lineWidth="0.25" lineEnd="ROUND"/></Line>
   </UserSpecial></Features>
   <Pad><Xform rotation="90"/><Location x="10" y="-19"/><StandardPrimitiveRef id="RECT_1"/>
    <PinRef componentRef="R1" pin="1"/></Pad>
   <Pad><Location x="10" y="-21"/><StandardPrimitiveRef id="CIRCLE_1"/>
    <PinRef componentRef="R1" pin="2"/></Pad>
   <Pad><Location x="3" y="-4"/><StandardPrimitiveRef id="CIRCLE_2"/></Pad>
  </Set></LayerFeature>
  <LayerFeature layerRef="B.Cu"><Set net="PWR">
   <Pad><Location x="10" y="-21"/><StandardPrimitiveRef id="CIRCLE_1"/>
    <PinRef componentRef="R1" pin="2"/></Pad>
   <Pad><Location x="3" y="-4"/><StandardPrimitiveRef id="CIRCLE_2"/></Pad>
  </Set></LayerFeature>
  <LayerFeature layerRef="DRILL">
   <Set net="PWR"><Hole diameter="0.8" platingStatus="PLATED" x="10" y="-21"/></Set>
   <Set><Hole diameter="2" platingStatus="NONPLATED" x="13" y="-20"/></Set>
  </LayerFeature></Step></CadData></Ecad></IPC-2581>'''


def audit(tmp_path, xml=XML, source=SOURCE):
    board, export = tmp_path / "board.kicad_pcb", tmp_path / "board.xml"
    board.write_text(source)
    export.write_text(xml)
    return inspect_geometry(board, export)


def test_matching_export_passes_with_rotation_reversed_track_and_holes(tmp_path):
    report = audit(tmp_path)
    assert report["passed"], report
    # Pad entries: R1.1 on F.Cu, R1.2 on both layers, via pad on both layers.
    assert report["tracks"]["matched"] == 1
    assert report["pads"]["matched"] == 5
    assert report["pad_holes"]["matched"] == 2
    assert report["unsupported"] == {}


@pytest.mark.parametrize("old,new,gate", [
    ('lineWidth="0.25"', 'lineWidth="0.26"', "tracks"),
    ('startX="0" startY="0"', 'startX="0.001" startY="0"', "tracks"),
    ('<Location x="10" y="-19"/>', '<Location x="10.001" y="-19"/>', "pads"),
    ('<RectCenter width="2" height="1"/>', '<RectCenter width="2" height="1.1"/>', "pads"),
    ('<Xform rotation="90"/>', '<Xform rotation="0"/>', "pads"),
    ('pin="1"', 'pin="9"', "pads"),
    ('<Circle diameter="0.6"/>', '<Circle diameter="0.7"/>', "pads"),
    ('diameter="0.8" platingStatus="PLATED"', 'diameter="0.9" platingStatus="PLATED"', "pad_holes"),
    ('platingStatus="NONPLATED"', 'platingStatus="PLATED"', "pad_holes"),
    ('x="10" y="-21"/></Set>', 'x="10.001" y="-21"/></Set>', "pad_holes"),
])
def test_changed_values_fail_despite_unchanged_counts(tmp_path, old, new, gate):
    assert old in XML
    report = audit(tmp_path, XML.replace(old, new))
    assert not report["gates"][gate]
    assert not report["passed"]


def test_missing_exported_track_fails(tmp_path):
    start = XML.index("   <Features>")
    end = XML.index("</Features>") + len("</Features>")
    assert not audit(tmp_path, XML[:start] + XML[end:])["gates"]["tracks"]


def test_wrong_net_fails_tracks_and_pads(tmp_path):
    report = audit(tmp_path, XML.replace('net="PWR"', 'net="GND"', 1))
    assert not report["gates"]["tracks"] or not report["gates"]["pads"]
    assert not report["passed"]


@pytest.mark.parametrize("source", [
    SOURCE.replace(' (segment', ' (arc (start 0 0) (mid 1 1) (end 2 0) (width 0.25) (layer "F.Cu") (net "PWR"))\n (segment'),
    SOURCE.replace("smd rect", "smd custom"),
    SOURCE.replace("(drill 0.8)", "(drill oval 0.8 1.2)"),
    SOURCE.replace('(layers "F.Cu" "B.Cu") (net "PWR")', '(layers "F.Cu" "In1.Cu") (net "PWR")'),
])
def test_unsupported_source_features_fail_instead_of_passing(tmp_path, source):
    assert source != SOURCE
    report = audit(tmp_path, source=source)
    assert report["unsupported"]
    assert not report["passed"]


def test_exported_arc_and_custom_pad_fail(tmp_path):
    arc = XML.replace("<Features><UserSpecial>", '<Features><UserSpecial><Arc startX="0" startY="0" endX="1" endY="1"/>', 1)
    assert audit(tmp_path, arc)["unsupported"] == {"track arcs": 1}
    custom = XML.replace('<StandardPrimitiveRef id="CIRCLE_2"/></Pad>', '<UserPrimitiveRef id="U1"/></Pad>', 1)
    assert audit(tmp_path, custom)["unsupported"] == {"exported custom pad": 1}


def test_unsupported_export_rejected(tmp_path):
    for xml in (XML.replace('units="MILLIMETER"/><CadData>', 'units="INCH"/><CadData>', 1),
                XML.replace('revision="C"', 'revision="B"')):
        with pytest.raises(ValueError):
            audit(tmp_path, xml)


def test_board_without_tracks_cannot_pass_the_track_gate(tmp_path):
    source = SOURCE.replace(' (segment (start 5 0) (end 0 0) (width 0.25) (layer "F.Cu") (net "PWR"))', "")
    assert source != SOURCE
    assert not audit(tmp_path, source=source)["gates"]["tracks"]


# Local-only real boards live in tests/test boards/, which is git-ignored, so CI skips this.
# It needs an export made by experiments/exp06_kicad_export.py into <board folder>/_exp06/.
LOCAL = sorted((Path(__file__).parent / "test boards").glob("*/_exp06/board.xml"))


@pytest.mark.skipif(not LOCAL, reason="no local exported test boards")
@pytest.mark.parametrize("export", LOCAL, ids=lambda p: p.parent.parent.name)
def test_local_real_boards_match_their_export(export):
    boards = sorted(export.parent.parent.glob("*.kicad_pcb"))
    assert len(boards) == 1
    report = inspect_geometry(boards[0], export)
    assert report["passed"], {k: report[k] for k in ("gates", "unsupported")}

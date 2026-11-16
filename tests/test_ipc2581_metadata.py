"""IPC-2581 header/stackup metadata reader: hand-written XML only, no real or private boards.

The element layout follows the files used by the existing export audits: Layer definitions
in CadData, one Stackup/StackupGroup, StackupLayer rows carrying layerOrGroupRef, thickness
and sequence. The stackup below is deliberately asymmetric, so a reversed or mirrored Z
computation cannot pass.
"""

from dataclasses import FrozenInstanceError, fields
import hashlib

import pytest

from pdnlab_dc.board import BoardModel
from pdnlab_dc.ipc2581_metadata import (NAMESPACE, XY_TRANSFORM, Ipc2581Metadata,
                                        parse_ipc2581_metadata, read_ipc2581_metadata)

LAYERS = [("F.Mask", "SOLDERMASK"), ("F.Cu", "CONDUCTOR"), ("DIELECTRIC_1", "DIELPREG"),
          ("In1.Cu", "CONDUCTOR"), ("DIELECTRIC_2", "DIELCORE"), ("In2.Cu", "CONDUCTOR"),
          ("DIELECTRIC_3", "DIELPREG"), ("B.Cu", "CONDUCTOR"), ("B.Mask", "SOLDERMASK"),
          ("F.Cu_B.Cu", "DRILL")]
# (layer, thickness in mm, sequence)
STACK = [("F.Mask", "0.010", 0), ("F.Cu", "0.035", 1), ("DIELECTRIC_1", "0.1", 2),
         ("In1.Cu", "0.0175", 3), ("DIELECTRIC_2", "1.24", 4), ("In2.Cu", "0.07", 5),
         ("DIELECTRIC_3", "0.2", 6), ("B.Cu", "0.018", 7), ("B.Mask", "0.010", 8)]
HISTORY = ('<HistoryRecord number="1" software="KiCad EDA"><FileRevision fileRevisionId="1">'
           '<SoftwarePackage name="KiCad" revision="10.0.1" vendor="KiCad EDA"/>'
           '</FileRevision></HistoryRecord>')
HEADER = '<CadHeader units="MILLIMETER"/>'
DICTIONARY = '<DictionaryStandard units="MILLIMETER"/>'
# Copper that this reader must leave alone: a pad at a nonzero position.
STEP = ('<Step name="board"><LayerFeature layerRef="F.Cu"><Set net="PWR">'
        '<Pad><Location x="2.0" y="-5.0"/><StandardPrimitiveRef id="C"/></Pad>'
        '</Set></LayerFeature></Step>')


def layer_xml(layers):
    return "".join(f'<Layer name="{n}" layerFunction="{f}"/>' if f else f'<Layer name="{n}"/>'
                   for n, f in layers)


def rows_xml(stack):
    return "".join("<StackupLayer"
                   + "".join(f' {k}="{v}"' for k, v in
                             zip(("layerOrGroupRef", "thickness", "sequence"), row) if v is not None)
                   + "/>" for row in stack)


def stackup_xml(stack):
    return f'<Stackup name="s"><StackupGroup name="g">{rows_xml(stack)}</StackupGroup></Stackup>'


def export(layers=LAYERS, stack=STACK, *, root="IPC-2581", ns=NAMESPACE, revision="C",
           header=HEADER, dictionary=DICTIONARY, history=HISTORY, stackup=None, cad=None, ecad=None):
    if stackup is None:
        stackup = stackup_xml(stack)
    if cad is None:
        cad = f"<CadData>{layer_xml(layers)}{stackup}{STEP}</CadData>"
    if ecad is None:
        ecad = f'<Ecad name="Design">{header}{cad}</Ecad>'
    attributes = (f' revision="{revision}"' if revision else "") + (f' xmlns="{ns}"' if ns else "")
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<{root}{attributes}>'
            f"<Content>{dictionary}</Content>{history}{ecad}</{root}>").encode()


def with_row(name, thickness=None, sequence=None):
    """STACK with one row's thickness and/or sequence replaced."""
    return [(n, thickness if n == name and thickness is not None else t,
             sequence if n == name and sequence is not None else s) for n, t, s in STACK]


def without(name, rows):
    return [row for row in rows if row[0] != name]


def test_asymmetric_stackup_gives_copper_centres_and_thicknesses_in_metres():
    meta = parse_ipc2581_metadata(export())
    assert [(c.id, c.label) for c in meta.copper_layers] == [(n, n) for n in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")]
    # mm -> m exactly once: 0.035 mm is 35 um, not 35 mm or 35 nm.
    assert [c.thickness_m for c in meta.copper_layers] == [35e-6, 17.5e-6, 70e-6, 18e-6]
    # Z from the first copper's outer face, e.g. In2.Cu: 0.035+0.1+0.0175+1.24+0.07/2 = 1.4275 mm.
    # The 0.010 mm mask above F.Cu is outside the copper span and must not shift Z.
    assert [c.z_m for c in meta.copper_layers] == [17.5e-6, 143.75e-6, 1427.5e-6, 1671.5e-6]
    # The neutral model accepts the layers; this reader does not itself return a board.
    assert BoardModel(meta.source, meta.copper_layers).layers == meta.copper_layers


def test_sequence_attribute_orders_the_stack_not_document_order():
    reference = parse_ipc2581_metadata(export())
    assert parse_ipc2581_metadata(export(stack=STACK[::-1])).copper_layers == reference.copper_layers
    assert parse_ipc2581_metadata(export(layers=LAYERS[::-1])).copper_layers == reference.copper_layers
    # Reversing the sequence numbers puts B.Cu first; the asymmetric stack then gives other centres.
    flipped = parse_ipc2581_metadata(export(stack=[(n, t, 8-s) for n, t, s in STACK]))
    assert [c.id for c in flipped.copper_layers] == ["B.Cu", "In2.Cu", "In1.Cu", "F.Cu"]
    assert [c.z_m for c in flipped.copper_layers] == [9e-6, 253e-6, 1536.75e-6, 1663e-6]


def test_rows_outside_the_copper_span_do_not_contribute_to_z():
    reference = parse_ipc2581_metadata(export())
    layers = [("COATING_TOP", "COATINGCOND"), *LAYERS, ("COATING_BOTTOM", "COATINGCOND")]
    stack = [("COATING_TOP", "0.0", 0), *[(n, "0.5" if "Mask" in n else t, s+1) for n, t, s in STACK],
             ("COATING_BOTTOM", "0.0", 10)]
    assert parse_ipc2581_metadata(export(layers, stack)).copper_layers == reference.copper_layers
    bare = [row for row in STACK if "Mask" not in row[0]]
    assert parse_ipc2581_metadata(export(stack=bare)).copper_layers == reference.copper_layers


def test_single_copper_layer_and_exponent_or_padded_decimal_text():
    one = parse_ipc2581_metadata(export([("Cu", "CONDUCTOR")], [("Cu", " 3.5e-2 ", 4)]))
    assert [(c.id, c.z_m, c.thickness_m) for c in one.copper_layers] == [("Cu", 17.5e-6, 35e-6)]


def test_source_records_actual_bytes_format_units_and_exporter(tmp_path):
    data = export()
    meta = parse_ipc2581_metadata(data)
    assert meta.source.input_sha256 == hashlib.sha256(data).hexdigest()
    assert (meta.source.format, meta.source.revision, meta.source.original_units) == ("IPC-2581", "C", "mm")
    assert meta.source.exporter_version == "KiCad 10.0.1"
    path = tmp_path / "board.xml"
    path.write_bytes(data)
    assert read_ipc2581_metadata(path) == meta
    assert parse_ipc2581_metadata(bytearray(data)) == meta
    # Same content, different bytes: the hash follows the bytes, the layers do not change.
    padded = parse_ipc2581_metadata(data + b"\n")
    assert padded.source.input_sha256 == hashlib.sha256(data + b"\n").hexdigest() != meta.source.input_sha256
    assert padded.copper_layers == meta.copper_layers


def test_xy_transform_is_recorded_once_and_nothing_else_is_imported():
    meta = parse_ipc2581_metadata(export())
    # Source (x, y) mm -> (x*1e-3, -y*1e-3) m: recorded for the geometry reader, applied to nothing.
    assert meta.source.xy_transform == XY_TRANSFORM == (1e-3, 0., 0., 0., -1e-3, 0.)
    assert isinstance(meta, Ipc2581Metadata) and not isinstance(meta, BoardModel)
    assert [f.name for f in fields(meta)] == ["source", "copper_layers"]  # No nets, features, pads, drills.
    assert any("not read" in a for a in meta.source.assumptions)
    assert any("has not been applied" in a for a in meta.source.assumptions)
    with pytest.raises(FrozenInstanceError):
        meta.copper_layers = ()


@pytest.mark.parametrize("history", [
    "",
    '<HistoryRecord software="KiCad EDA"/>',
    '<HistoryRecord><FileRevision><SoftwarePackage name="KiCad"/></FileRevision></HistoryRecord>',
    '<HistoryRecord><FileRevision><SoftwarePackage name=" " revision="10.0.1"/></FileRevision></HistoryRecord>',
    '<HistoryRecord><FileRevision><SoftwarePackage name="A" revision="1"/>'
    '<SoftwarePackage name="B" revision="2"/></FileRevision></HistoryRecord>',
    HISTORY*2,
])
def test_exporter_version_is_none_unless_exactly_one_complete_package_is_declared(history):
    assert parse_ipc2581_metadata(export(history=history)).source.exporter_version is None


@pytest.mark.parametrize("kwargs, message", [
    (dict(revision="B"), "unsupported IPC-2581 revision 'B'"),
    (dict(revision=""), "unsupported IPC-2581 revision None"),
    (dict(ns=""), "expected an IPC-2581 root element"),
    (dict(ns="http://example.com/other"), "expected an IPC-2581 root element"),
    (dict(root="Board"), "expected an IPC-2581 root element"),
    (dict(header='<CadHeader units="INCH"/>'), "unsupported units 'INCH' on CadHeader"),
    (dict(header='<CadHeader units="MICRON"/>'), "unsupported units 'MICRON' on CadHeader"),
    (dict(header='<CadHeader units="millimeter"/>'), "unsupported units 'millimeter'"),
    (dict(header="<CadHeader/>"), "unsupported units None on CadHeader"),
    (dict(header=""), "exactly one CadHeader in Ecad, found 0"),
    (dict(header=HEADER*2), "exactly one CadHeader in Ecad, found 2"),
    (dict(dictionary='<DictionaryUser units="INCH"/>'), "unsupported units 'INCH' on DictionaryUser"),
    (dict(ecad=""), "exactly one Ecad in IPC-2581, found 0"),
    (dict(cad=""), "exactly one CadData in Ecad, found 0"),
    (dict(stackup=""), "an explicit stackup is required"),
    (dict(stackup=stackup_xml(STACK)*2), "exactly one Stackup"),
    (dict(stackup="<Stackup/>"), "exactly one StackupGroup in Stackup, found 0"),
    (dict(stackup=f"<Stackup><StackupGroup>{rows_xml(STACK[:5])}</StackupGroup>"
                  f"<StackupGroup>{rows_xml(STACK[5:])}</StackupGroup></Stackup>"),
     "exactly one StackupGroup in Stackup, found 2"),
])
def test_unsupported_or_missing_header_metadata_is_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        parse_ipc2581_metadata(export(**kwargs))


@pytest.mark.parametrize("layers, message", [
    ([*LAYERS, ("F.Cu", "CONDUCTOR")], "duplicate Layer definition 'F.Cu'"),
    ([*LAYERS, ("F.Mask", "SOLDERPASTE")], "duplicate Layer definition 'F.Mask'"),
    ([*LAYERS, ("", "CONDUCTOR")], "Layer without a name"),
    ([*without("In1.Cu", LAYERS), ("In1.Cu", "")], "Layer 'In1.Cu' has no layerFunction"),
    ([*without("In1.Cu", LAYERS), ("In1.Cu", "PLANE")], "layerFunction PLANE is not supported"),
    ([*LAYERS, ("In9.Cu", "CONDUCTOR")], r"copper layers \['In9.Cu'\] are defined but absent"),
    ([*without("In1.Cu", LAYERS), ("In1.Cu", "DOCUMENT")], r"'In1.Cu' \(DOCUMENT\) lies between copper"),
    (without("DIELECTRIC_2", LAYERS), "references undefined layer 'DIELECTRIC_2'"),
    ([(n, "DIELCORE" if f == "CONDUCTOR" else f) for n, f in LAYERS], "contains no CONDUCTOR layer"),
])
def test_invalid_layer_definitions_are_rejected(layers, message):
    with pytest.raises(ValueError, match=message):
        parse_ipc2581_metadata(export(layers))


@pytest.mark.parametrize("stack, message", [
    ([*STACK, ("In1.Cu", "0.0175", 9)], "'In1.Cu' appears more than once"),
    ([*STACK, (None, "0.1", 9)], "references undefined layer None"),
    (with_row("In1.Cu", sequence=4), "duplicate sequence 4"),
    (with_row("B.Mask", sequence=0), "duplicate sequence 0"),
    (with_row("In1.Cu", sequence="3.0"), "sequence '3.0' is not a nonnegative integer"),
    (with_row("In1.Cu", sequence="-3"), "sequence '-3' is not a nonnegative integer"),
    ([("In1.Cu", "0.0175", None), *without("In1.Cu", STACK)], "sequence None is not a nonnegative integer"),
    ([("In1.Cu", None, 3), *without("In1.Cu", STACK)], "'In1.Cu': thickness None is not a decimal"),
    (with_row("In1.Cu", thickness=""), "thickness '' is not a decimal"),
    (with_row("In1.Cu", thickness="0,0175"), "thickness '0,0175' is not a decimal"),
    (with_row("In1.Cu", thickness="1_0"), "thickness '1_0' is not a decimal"),
    (with_row("DIELECTRIC_2", thickness="nan"), "'DIELECTRIC_2': thickness 'nan' is not a decimal"),
    (with_row("DIELECTRIC_2", thickness="inf"), "thickness 'inf' is not a decimal"),
    (with_row("F.Cu", thickness="0.0"), "'F.Cu': thickness '0.0' must be finite and positive"),
    (with_row("B.Cu", thickness="-0.018"), "'B.Cu': thickness '-0.018' must be finite and positive"),
    (with_row("DIELECTRIC_1", thickness="0"), "'DIELECTRIC_1': thickness '0' must be finite and positive"),
    (with_row("DIELECTRIC_3", thickness="1e400"), "thickness '1e400' must be finite and positive"),
    (with_row("DIELECTRIC_3", thickness="1e-400"), "thickness '1e-400' must be finite and positive"),
    (without("DIELECTRIC_2", STACK), "'In1.Cu' and 'In2.Cu' have no dielectric between them"),
    (without("In1.Cu", without("F.Cu", STACK)), r"copper layers \['F.Cu', 'In1.Cu'\] are defined but absent"),
    ([], "are defined but absent from the stackup"),
])
def test_invalid_stackup_rows_are_rejected(stack, message):
    with pytest.raises(ValueError, match=message):
        parse_ipc2581_metadata(export(stack=stack))


def test_malformed_compressed_or_non_byte_input_is_rejected():
    with pytest.raises(ValueError, match="malformed IPC-2581 XML"):
        parse_ipc2581_metadata(export()[:-20])
    with pytest.raises(ValueError, match="malformed IPC-2581 XML"):
        parse_ipc2581_metadata(b"")
    with pytest.raises(ValueError, match="compressed IPC-2581 archives are not supported"):
        parse_ipc2581_metadata(b"PK\x03\x04" + export())
    with pytest.raises(TypeError, match="file's bytes"):
        parse_ipc2581_metadata(export().decode())

"""IPC-2581 header and stackup metadata only. This is not a board importer.

Reads source provenance and ordered copper layers from an uncompressed
revision C file in millimetres with one explicit stackup. Nets, tracks, pads,
zones, drills and components are not read, so the result is deliberately not
a BoardModel. Anything outside the supported subset raises ValueError.

Conventions, also recorded in SourceInfo.assumptions:
* Z is zero at the outer face of the first copper layer in stackup sequence
  and increases with sequence; CopperLayer.z_m is the copper centre.
* xy_transform maps source millimetres to stored metres and reverses IPC Y
  once. It is recorded for the later geometry reader; nothing here applies it.
"""

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from pdnlab_dc.board import CopperLayer, SourceInfo

NAMESPACE = "http://webstds.ipc.org/2581"
# Source XY (mm) -> stored SI XY (m), in SourceInfo order (a, b, tx, c, d, ty).
XY_TRANSFORM = (1e-3, 0.0, 0.0, 0.0, -1e-3, 0.0)
# Conventions shared with readers that build on this one (ipc2581_fills).
CONVENTIONS = (
    "Z is zero at the outer face of the first copper layer in stackup sequence and increases "
    "with sequence; z_m is each copper centre.",
    "Copper layers are layerFunction CONDUCTOR; stackup rows outside the copper span do not "
    "contribute to Z.",
)
ASSUMPTIONS = (
    "Header and stackup metadata only: nets, copper geometry, pads and drills were not read.",
    "xy_transform (source mm to stored m, IPC Y reversed once) is recorded for the geometry "
    "reader and has not been applied to anything.",
) + CONVENTIONS

_MM = Decimal("0.001")
_DECIMAL = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
# Other IPC-2581 conductor functions: rejected, so their copper is never silently dropped.
_OTHER_CONDUCTORS = {"SIGNAL", "PLANE", "MIXED", "CONDFOIL", "CONDFILM"}


@dataclass(frozen=True)
class Ipc2581Metadata:
    """Header and stackup facts only: no nets, copper geometry, pads or drills."""
    source: SourceInfo
    copper_layers: tuple[CopperLayer, ...]


def _q(tag):
    return f"{{{NAMESPACE}}}{tag}"


def _one(parent, tag, context):
    found = parent.findall(_q(tag))
    if len(found) != 1:
        raise ValueError(f"expected exactly one {tag} in {context}, found {len(found)}")
    return found[0]


def _check_units(element, context, required):
    units = element.get("units")
    if units is None and not required:
        return
    if units != "MILLIMETER":
        raise ValueError(f"unsupported units {units!r} on {context}: only MILLIMETER is read")


def _layer_functions(cad):
    functions = {}
    for layer in cad.findall(_q("Layer")):
        name, function = layer.get("name"), layer.get("layerFunction")
        if not name or not name.strip():
            raise ValueError("CadData Layer without a name")
        if name in functions:
            raise ValueError(f"duplicate Layer definition {name!r}")
        if not function:
            raise ValueError(f"Layer {name!r} has no layerFunction")
        if function in _OTHER_CONDUCTORS:
            raise ValueError(f"Layer {name!r}: layerFunction {function} is not supported; "
                             "copper layers must be CONDUCTOR")
        functions[name] = function
    return functions


def _stack_rows(cad, functions):
    """(sequence, layer name, thickness text) in stackup sequence order."""
    group = _one(_one(cad, "Stackup", "CadData (an explicit stackup is required)"),
                 "StackupGroup", "Stackup")
    rows, names, sequences = [], set(), set()
    for row in group.findall(_q("StackupLayer")):
        name, sequence = row.get("layerOrGroupRef"), row.get("sequence")
        if name not in functions:
            raise ValueError(f"StackupLayer references undefined layer {name!r}")
        if name in names:
            raise ValueError(f"layer {name!r} appears more than once in the stackup")
        if sequence is None or not re.fullmatch(r"\d+", sequence):
            raise ValueError(f"stackup layer {name!r}: sequence {sequence!r} is not a "
                             "nonnegative integer")
        if int(sequence) in sequences:
            raise ValueError(f"stackup layer {name!r}: duplicate sequence {sequence}")
        names.add(name)
        sequences.add(int(sequence))
        rows.append((int(sequence), name, row.get("thickness")))
    return sorted(rows)


def _thickness_mm(text, name):
    if text is None or not _DECIMAL.fullmatch(text.strip()):
        raise ValueError(f"stackup layer {name!r}: thickness {text!r} is not a decimal number")
    value = Decimal(text.strip())
    metres = float(value*_MM)
    if not math.isfinite(metres) or metres <= 0:
        raise ValueError(f"stackup layer {name!r}: thickness {text!r} must be finite and positive")
    return value


def _copper_layers(rows, functions):
    stacked = {name for _, name, _ in rows}
    absent = sorted(name for name, function in functions.items()
                    if function == "CONDUCTOR" and name not in stacked)
    if absent:
        raise ValueError(f"copper layers {absent} are defined but absent from the stackup")
    copper = [i for i, (_, name, _) in enumerate(rows) if functions[name] == "CONDUCTOR"]
    if not copper:
        raise ValueError("the stackup contains no CONDUCTOR layer")
    # Exact decimal sums in source millimetres; one rounding per stored value.
    z, layers, previous_copper = Decimal(0), [], None
    for _, name, text in rows[copper[0]:copper[-1]+1]:
        function = functions[name]
        if function == "CONDUCTOR":
            if previous_copper is not None:
                raise ValueError(f"copper layers {previous_copper!r} and {name!r} have no "
                                 "dielectric between them")
            thickness = _thickness_mm(text, name)
            layers.append(CopperLayer(name, name, float((z+thickness/2)*_MM), float(thickness*_MM)))
            previous_copper = name
        elif function.startswith("DIEL"):
            thickness = _thickness_mm(text, name)
            previous_copper = None
        else:
            raise ValueError(f"stackup layer {name!r} ({function}) lies between copper layers; "
                             "only CONDUCTOR and dielectric layers are supported there")
        z += thickness
    return tuple(layers)


def _exporter_version(root):
    """'name revision' when the file declares exactly one complete SoftwarePackage, else None."""
    packages = root.findall(f"{_q('HistoryRecord')}/{_q('FileRevision')}/{_q('SoftwarePackage')}")
    if len(packages) != 1:
        return None
    name, revision = (packages[0].get(key, "").strip() for key in ("name", "revision"))
    return f"{name} {revision}" if name and revision else None


def parse_ipc2581_metadata(data):
    """Provenance and ordered copper layers from the bytes of an IPC-2581 C file."""
    return parse_ipc2581_tree(data)[1]


def parse_ipc2581_tree(data):
    """(parsed root element, Ipc2581Metadata) after the checks above, from one parse.

    For readers that need more of the same file; they must not repeat these checks.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("input must be the file's bytes, so that the recorded hash is the source's")
    data = bytes(data)
    if data.startswith(b"PK"):
        raise ValueError("compressed IPC-2581 archives are not supported; export uncompressed XML")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"malformed IPC-2581 XML: {exc}") from exc
    if root.tag != _q("IPC-2581"):
        raise ValueError(f"expected an IPC-2581 root element in namespace {NAMESPACE}, "
                         f"found {root.tag!r}")
    revision = root.get("revision")
    if revision != "C":
        raise ValueError(f"unsupported IPC-2581 revision {revision!r}: only revision C is read")
    ecad = _one(root, "Ecad", "IPC-2581")
    _check_units(_one(ecad, "CadHeader", "Ecad"), "CadHeader", required=True)
    for content in root.findall(_q("Content")):
        for entry in content:
            tag = entry.tag.rsplit("}", 1)[-1]
            if entry.tag == _q(tag) and tag.startswith("Dictionary"):
                _check_units(entry, tag, required=False)
    cad = _one(ecad, "CadData", "Ecad")
    functions = _layer_functions(cad)
    layers = _copper_layers(_stack_rows(cad, functions), functions)
    source = SourceInfo(hashlib.sha256(data).hexdigest(), "IPC-2581", revision, "mm",
                        XY_TRANSFORM, _exporter_version(root), ASSUMPTIONS)
    return root, Ipc2581Metadata(source, layers)


def read_ipc2581_metadata(path):
    """Read one file once; the same bytes are hashed and parsed."""
    return parse_ipc2581_metadata(Path(path).read_bytes())

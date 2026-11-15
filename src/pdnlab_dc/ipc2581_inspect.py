"""Phase-0 export audit, not a production board importer.

Compare saved KiCad data with KiCad 10's IPC-2581 C export. This intentionally
supports a narrow evidence path: explicit stackups, linear saved fill polygons,
netted pads, and circular vias. Tracks and pad copper shapes are not compared, so
a pass here does not settle the importer route. Unsupported/missing data must
not become a pass.
Coordinates are compared on a 1 nm grid, after reversing the export's Y axis.
"""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def parse_sexpr(text):
    """Read nested KiCad lists without loading deprecated pcbnew bindings."""
    tokens = re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+', text)
    stack, roots = [], []
    for token in tokens:
        if token == "(":
            item = []
            (stack[-1] if stack else roots).append(item)
            stack.append(item)
        elif token == ")":
            if not stack:
                raise ValueError("unexpected closing parenthesis")
            stack.pop()
        else:
            if not stack:
                raise ValueError("atom outside a list")
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    if stack or len(roots) != 1:
        raise ValueError("expected one balanced S-expression")
    return roots[0]


def _children(node, name):
    return [x for x in node if isinstance(x, list) and x and x[0] == name]


def _one(node, name):
    items = _children(node, name)
    if len(items) != 1:
        raise ValueError(f"expected exactly one {name!r} entry")
    return items[0]


def _nm(mm):
    return round(float(mm) * 1e6)


def _polygon(points):
    """Canonical closed polygon cycle: ignore start vertex and orientation."""
    points = tuple(points)
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        raise ValueError("polygon needs at least three vertices")
    candidates = []
    for cycle in [points, points[::-1]]:
        minimum = min(cycle)
        for i, point in enumerate(cycle):
            if point == minimum:
                candidates.append(cycle[i:] + cycle[:i])
    return min(candidates)


def _net_namer(root):
    """Return net reference -> name. KiCad 10 (format 20260206) names nets inline and
    writes no id table; older files reference nets by id through a top-level table."""
    table = {n[1]: n[2] for n in _children(root, "net")}
    if not table:
        return lambda ref: ref

    def name(ref):
        if ref not in table:
            raise ValueError(f"net id {ref!r} is not in the board's net table")
        return table[ref]
    return name


def _source_data(path):
    root = parse_sexpr(Path(path).read_text(encoding="utf-8"))
    if not root or root[0] != "kicad_pcb":
        raise ValueError("expected a native kicad_pcb file")
    net_name = _net_namer(root)
    stack = _one(_one(root, "setup"), "stackup")
    thicknesses = {}
    for layer in _children(stack, "layer"):
        if _children(layer, "thickness"):
            thicknesses[layer[1]] = _nm(_one(layer, "thickness")[1])
    pins = set()
    for footprint in _children(root, "footprint"):
        refs = [p[2] for p in _children(footprint, "property") if p[1] == "Reference"]
        if not refs:
            refs = [p[2] for p in _children(footprint, "fp_text") if p[1] == "reference"]
        if len(refs) != 1:
            raise ValueError("footprint must have one reference")
        for pad in _children(footprint, "pad"):
            net = _children(pad, "net")
            if net and net_name(net[0][1]):
                pins.add((refs[0], pad[1], net_name(net[0][1])))
    fills = Counter()
    for zone in _children(root, "zone"):
        for fill in _children(zone, "filled_polygon"):
            layer = _one(fill, "layer")[1]
            net = net_name(_one(zone, "net")[1])
            points = [(_nm(p[1]), _nm(p[2])) for p in _children(_one(fill, "pts"), "xy")]
            fills[(layer, net, _polygon(points))] += 1
    vias = Counter()
    for via in _children(root, "via"):
        at, drill, layers = _one(via, "at"), _one(via, "drill"), _one(via, "layers")
        vias[(_nm(at[1]), _nm(at[2]), _nm(drill[1]),
              net_name(_one(via, "net")[1]), layers[1], layers[2])] += 1
    return dict(thicknesses=thicknesses, pins=pins, fills=fills, vias=vias,
                zones=len(_children(root, "zone")),
                footprints=len(_children(root, "footprint")))


def _export_data(path):
    root = ET.parse(path).getroot()
    ns = {"i": "http://webstds.ipc.org/2581"}
    if root.tag != "{http://webstds.ipc.org/2581}IPC-2581" or root.get("revision") != "C":
        raise ValueError("this audit requires IPC-2581 revision C")
    header = root.find(".//i:CadHeader", ns)
    if header is None or header.get("units") != "MILLIMETER":
        raise ValueError("this audit requires MILLIMETER units")
    thicknesses = {x.get("layerOrGroupRef"): _nm(x.get("thickness"))
                   for x in root.findall(".//i:StackupLayer", ns)}
    spans = {}
    copper = set()
    for layer in root.findall(".//i:CadData/i:Layer", ns):
        if layer.get("layerFunction") == "CONDUCTOR":
            copper.add(layer.get("name"))
        span = layer.find("i:Span", ns)
        if span is not None:
            spans[layer.get("name")] = (span.get("fromLayer"), span.get("toLayer"))
    pins, fills, vias = set(), Counter(), Counter()
    parents = {child: parent for parent in root.iter() for child in parent}
    for layer in root.findall(".//i:LayerFeature", ns):
        layer_name = layer.get("layerRef")
        for group in layer.findall("i:Set", ns):
            net = group.get("net", "")
            if layer_name in copper:
                for pin in group.findall(".//i:PinRef", ns):
                    if net:
                        pins.add((pin.get("componentRef"), pin.get("pin"), net))
                for polygon in group.findall(".//i:Contour/i:Polygon", ns):
                    ancestor = parents[polygon]
                    while ancestor is not group:
                        if ancestor.find("i:Xform", ns) is not None:
                            raise ValueError("transformed contour is outside this audit's scope")
                        location = ancestor.find("i:Location", ns)
                        if location is not None and (_nm(location.get("x")) or _nm(location.get("y"))):
                            raise ValueError("translated contour is outside this audit's scope")
                        ancestor = parents[ancestor]
                    points = []
                    for point in polygon:
                        if point.tag.rsplit("}", 1)[-1] not in {"PolyBegin", "PolyStepSegment"}:
                            raise ValueError("curved exported contour is outside this audit's scope")
                        points.append((_nm(point.get("x")), -_nm(point.get("y"))))
                    fills[(layer_name, net, _polygon(points))] += 1
            for hole in group.findall("i:Hole", ns):
                if hole.get("platingStatus") == "VIA":
                    if layer_name not in spans:
                        raise ValueError("via drill layer has no layer span")
                    vias[(_nm(hole.get("x")), -_nm(hole.get("y")),
                          _nm(hole.get("diameter")), net, *spans[layer_name])] += 1
    return dict(thicknesses=thicknesses, pins=pins, fills=fills, vias=vias,
                copper_layers=sorted(copper))


def inspect_export(board_path, xml_path):
    """Return measured coverage and a conservative route decision for this board."""
    source, exported = _source_data(board_path), _export_data(xml_path)
    # KiCad changes 'dielectric 1' to 'DIELECTRIC_1' in this export.
    expected_stack = {name.upper().replace(" ", "_") if name.startswith("dielectric ") else name: value
                      for name, value in source["thicknesses"].items()}
    stack_mismatch = {name: dict(source_nm=value, export_nm=exported["thicknesses"].get(name))
                      for name, value in expected_stack.items()
                      if exported["thicknesses"].get(name) != value}
    missing_pins = source["pins"] - exported["pins"]
    unexpected_pins = exported["pins"] - source["pins"]
    missing_fills = source["fills"] - exported["fills"]
    missing_vias = source["vias"] - exported["vias"]
    unexpected_vias = exported["vias"] - source["vias"]
    gates = dict(
        zone_fills=bool(source["fills"] and not missing_fills),
        stackup=bool(expected_stack and exported["copper_layers"] and not stack_mismatch
                     and all(expected_stack.get(layer, 0) > 0 for layer in exported["copper_layers"])),
        pin_to_net=bool(source["pins"] and not missing_pins and not unexpected_pins),
        via_holes=bool(source["vias"] and not missing_vias and not unexpected_vias),
    )
    passed = all(gates.values())
    return dict(
        board_sha256=hashlib.sha256(Path(board_path).read_bytes()).hexdigest(),
        export_sha256=hashlib.sha256(Path(xml_path).read_bytes()).hexdigest(),
        coordinate_comparison_grid_m=1e-9, export_y_axis_reversed=True,
        source_zones=source["zones"], source_footprints=source["footprints"],
        copper_layers=exported["copper_layers"], stackup_thickness_mm={k: v / 1e6 for k, v in expected_stack.items()},
        exported_stackup_thickness_mm={k: v / 1e6 for k, v in exported["thicknesses"].items()},
        stackup_mismatches=stack_mismatch,
        source_fill_polygons=source["fills"].total(), matched_fill_polygons=source["fills"].total() - missing_fills.total(),
        missing_fill_polygons=missing_fills.total(),
        source_pin_net_mappings=len(source["pins"]), matched_pin_net_mappings=len(source["pins"] - missing_pins),
        missing_pin_net_mappings=sorted(missing_pins), unexpected_pin_net_mappings=sorted(unexpected_pins),
        source_vias=source["vias"].total(), matched_vias=source["vias"].total() - missing_vias.total(),
        missing_vias=missing_vias.total(), unexpected_vias=unexpected_vias.total(),
        exported_via_diameters_mm=sorted({v[2] / 1e6 for v in exported["vias"]}),
        gates=gates, passed=passed,
        route_decision=("audited items match; IPC-2581 stays a provisional importer route until tracks and pad geometry are audited"
                        if passed else "incomplete coverage; investigate before choosing route"),
        limitations=["Not a complete board importer or mesh generator.",
                     "Track segments, track arcs and pad copper geometry are not compared.",
                     "Saved fills are compared; their freshness is not established.",
                     "Plating thickness is not inferred from hole diameter; require setup input.",
                     "No check of PTH/NPTH pad holes, slots, or actual via contacts on intermediate layers.",
                     "Coverage is evidence for this board and exporter version only."],
    )

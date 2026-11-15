"""Phase-0 export audit, part 2: tracks, pad copper and pad holes.

Compares saved KiCad data with KiCad 10's IPC-2581 C export, extending
ipc2581_inspect (fills, vias, pins, stackup). Same rules: this is evidence for one
board and exporter version, not an importer, and anything unsupported must fail the
gate instead of being skipped.

Supported: straight track segments, circular, rectangular and rounded-rectangle pads
(rotation in any angle, compared by axis-aligned extent), circular pad holes, through
vias. Unsupported and reported as a failing gate: track arcs, custom or polygonal pads,
oval pad holes, per-layer padstack modes, blind or buried vias.
Coordinates are compared on a 1 nm grid, after reversing the export's Y axis.
"""

from collections import Counter
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from pdnlab_dc.ipc2581_inspect import _children, _nm, _one, _net_namer, parse_sexpr

NS = {"i": "http://webstds.ipc.org/2581"}


def _tag(element):
    return element.tag.rsplit("}", 1)[-1]


def _rotate(x, y, degrees):
    """Rotate a footprint-local point; KiCad angles are counter-clockwise on screen, Y down."""
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return x * c + y * s, -x * s + y * c


def _extent(width, height, degrees):
    """Axis-aligned extent of a width x height rectangle rotated by degrees."""
    c, s = abs(math.cos(math.radians(degrees))), abs(math.sin(math.radians(degrees)))
    return width * c + height * s, width * s + height * c


def _angle(node):
    return float(node[3]) if len(node) > 3 else 0.0


def _source(path):
    root = parse_sexpr(Path(path).read_text(encoding="utf-8"))
    if not root or root[0] != "kicad_pcb":
        raise ValueError("expected a native kicad_pcb file")
    net_name = _net_namer(root)
    # KiCad lists copper layers outer-top first, outer-bottom last.
    copper = [item[1] for item in _one(root, "layers")[1:]
              if isinstance(item, list) and len(item) > 2 and item[2] in {"signal", "power", "mixed", "jumper"}]
    if len(copper) < 2:
        raise ValueError("board declares fewer than two copper layers")
    unsupported = Counter()

    tracks = Counter()
    for segment in _children(root, "segment"):
        a, b = _one(segment, "start"), _one(segment, "end")
        ends = sorted([(_nm(a[1]), _nm(a[2])), (_nm(b[1]), _nm(b[2]))])
        tracks[(_one(segment, "layer")[1], net_name(_one(segment, "net")[1]), *ends,
                _nm(_one(segment, "width")[1]))] += 1
    unsupported["track arcs"] += len(_children(root, "arc"))

    pads, holes = Counter(), Counter()
    for footprint in _children(root, "footprint"):
        at = _one(footprint, "at")
        fx, fy, frot = float(at[1]), float(at[2]), _angle(at)
        refs = [p[2] for p in _children(footprint, "property") if p[1] == "Reference"]
        if len(refs) != 1:
            raise ValueError("footprint must have one reference")
        for pad in _children(footprint, "pad"):
            pat = _one(pad, "at")
            lx, ly = _rotate(float(pat[1]), float(pat[2]), frot)
            x, y = fx + lx, fy + ly
            size = _one(pad, "size")
            kind, shape = pad[2], pad[3]
            if shape not in {"circle", "rect", "roundrect"}:
                unsupported[f"pad shape {shape}"] += 1
                continue
            if _children(pad, "padstack"):
                unsupported["per-layer padstack"] += 1
                continue
            nets = _children(pad, "net")
            net = net_name(nets[0][1]) if nets else ""
            ex, ey = _extent(float(size[1]), float(size[2]), _angle(pat))
            layers = _one(pad, "layers")[1:]
            on = set(copper) if "*.Cu" in layers else {name for name in layers if name in copper}
            if kind == "np_thru_hole":
                on = set()
            for layer in on:
                pads[(layer, refs[0], pad[1], net, _nm(x), _nm(y), _nm(ex), _nm(ey))] += 1
            drill = _children(pad, "drill")
            if drill:
                values = drill[0][1:]
                if values and values[0] == "oval":
                    unsupported["oval pad hole"] += 1
                else:
                    plated = "NONPLATED" if kind == "np_thru_hole" else "PLATED"
                    holes[(_nm(x), _nm(y), _nm(values[0]), plated)] += 1
    vias = Counter()
    for via in _children(root, "via"):
        at, layers = _one(via, "at"), _one(via, "layers")
        if len(layers) != 3 or {layers[1], layers[2]} != {copper[0], copper[-1]}:
            unsupported["blind or buried via"] += 1
            continue
        d = _nm(_one(via, "size")[1])
        for layer in copper:
            pads[(layer, "", "", net_name(_one(via, "net")[1]), _nm(at[1]), _nm(at[2]), d, d)] += 1
    return dict(tracks=tracks, pads=pads, holes=holes, unsupported=unsupported)


def _export(path):
    root = ET.parse(path).getroot()
    if root.tag != "{%s}IPC-2581" % NS["i"] or root.get("revision") != "C":
        raise ValueError("this audit requires IPC-2581 revision C")
    header = root.find(".//i:CadHeader", NS)
    if header is None or header.get("units") != "MILLIMETER":
        raise ValueError("this audit requires MILLIMETER units")
    standard = {}
    for entry in root.findall(".//i:DictionaryStandard/i:EntryStandard", NS):
        shape = entry[0]
        if _tag(shape) == "Circle":
            standard[entry.get("id")] = (_nm(shape.get("diameter")),) * 2
        elif _tag(shape) in {"RectCenter", "RectRound"}:
            standard[entry.get("id")] = (_nm(shape.get("width")), _nm(shape.get("height")))
    copper = {layer.get("name") for layer in root.findall(".//i:CadData/i:Layer", NS)
              if layer.get("layerFunction") == "CONDUCTOR"}
    unsupported = Counter()
    tracks, pads, holes = Counter(), Counter(), Counter()
    for feature in root.findall(".//i:LayerFeature", NS):
        layer = feature.get("layerRef")
        for group in feature.findall("i:Set", NS):
            net = group.get("net", "")
            if layer in copper:
                for line in group.findall(".//i:Line", NS):
                    ends = sorted([(_nm(line.get("startX")), -_nm(line.get("startY"))),
                                   (_nm(line.get("endX")), -_nm(line.get("endY")))])
                    tracks[(layer, net, *ends, _nm(line.find("i:LineDesc", NS).get("lineWidth")))] += 1
                for arc in group.findall(".//i:Arc", NS):
                    unsupported["track arcs"] += 1
                for pad in group.findall("i:Pad", NS):
                    primitive = pad.find("i:StandardPrimitiveRef", NS)
                    if primitive is None or primitive.get("id") not in standard:
                        unsupported["exported custom pad"] += 1
                        continue
                    location, pin = pad.find("i:Location", NS), pad.find("i:PinRef", NS)
                    xform = pad.find("i:Xform", NS)
                    if xform is not None and xform.get("mirror") == "true":
                        unsupported["mirrored exported pad"] += 1
                        continue
                    width, height = standard[primitive.get("id")]
                    ex, ey = _extent(width, height, float(xform.get("rotation", 0)) if xform is not None else 0.0)
                    pads[(layer, pin.get("componentRef") if pin is not None else "",
                          pin.get("pin") if pin is not None else "", net,
                          _nm(location.get("x")), -_nm(location.get("y")), round(ex), round(ey))] += 1
            else:
                for hole in group.findall("i:Hole", NS):
                    if hole.get("platingStatus") in {"PLATED", "NONPLATED"}:
                        holes[(_nm(hole.get("x")), -_nm(hole.get("y")), _nm(hole.get("diameter")),
                               hole.get("platingStatus"))] += 1
    return dict(tracks=tracks, pads=pads, holes=holes, unsupported=unsupported, copper=sorted(copper))


def _diff(source, exported, limit=8):
    missing, unexpected = source - exported, exported - source
    return dict(source=source.total(), matched=source.total() - missing.total(),
                missing=missing.total(), unexpected=unexpected.total(),
                missing_samples=[list(k) for k in sorted(missing)[:limit]],
                unexpected_samples=[list(k) for k in sorted(unexpected)[:limit]])


def inspect_geometry(board_path, xml_path):
    """Compare tracks, pad copper and pad holes; return counts, samples and gates."""
    source, exported = _source(board_path), _export(xml_path)
    # Exported pad entries for pad shapes this audit cannot read must not pass silently.
    unsupported = source["unsupported"] + exported["unsupported"]
    report = dict(tracks=_diff(source["tracks"], exported["tracks"]),
                  pads=_diff(source["pads"], exported["pads"]),
                  pad_holes=_diff(source["holes"], exported["holes"]),
                  unsupported={k: v for k, v in unsupported.items() if v})
    gates = {name: bool(report[name]["source"] and not report[name]["missing"]
                        and not report[name]["unexpected"] and not report["unsupported"])
             for name in ("tracks", "pads", "pad_holes")}
    report.update(gates=gates, passed=all(gates.values()),
                  limitations=["Pad corner radius, custom pads and slots are not compared.",
                               "Pad copper is compared by centre and axis-aligned extent only.",
                               "Whether a via or pad actually touches a fill on a layer is not checked.",
                               "Coverage is evidence for this board and exporter version only."])
    return report

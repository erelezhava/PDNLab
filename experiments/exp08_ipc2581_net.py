"""Read one net from a KiCad 10 IPC-2581 C export and solve it with the exp08 prototype.

exp08_real_net.py takes its copper from the saved .kicad_pcb file. This script builds the
same board description from `kicad-cli pcb export ipc2581` output instead, so the provisional
importer route of experiment 6 is exercised by a real solve, and compares the two readers:

    uv run python experiments/exp08_ipc2581_net.py BOARD.kicad_pcb BOARD.xml --net +3V3 \
        --source J4=J4.6,J4.10,J4.13,J4.16 --load U3.8,Y2.4 --out OUTDIR

It reports (1) the geometry both readers produce, item by item, and (2) the drop at every
load pin for every source, solved from each reader on the same mesh settings.

What the export does not carry, and what this script does about it:
* the pin's electrical type (KiCad's power_in): the load pins must be given with --load;
* a zone's thermal-relief gap, which exp08 uses to size the fine-mesh region around a
  through-hole pad: the solve is run twice from the export, once with the gap taken from
  the .kicad_pcb file (same mesh rule as the direct read) and once without it (what an
  importer that sees only the export would do).

Supported, and anything else is an error instead of being skipped: revision C in
millimetres; straight track segments; circular, rectangular and rounded-rectangle pads
rotated by a multiple of 90 degrees; circular plated holes; through vias; fill contours
made of straight segments, with or without cut-outs.

Gates (exit code 1 if any fails):
* geometry: every item equal, pad outlines and centres within 1 nm;
* same reader result: with the relief gap supplied the drops equal the direct read to 1e-9
  (identical geometry gives an identical mesh);
* missing relief gap: without it every drop is within 10 uV + 1 % of the direct read, the
  size of release gate 5 for a mesh change.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import subprocess
import xml.etree.ElementTree as ET

import numpy as np
from shapely import affinity
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union
from shapely.validation import make_valid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exp08_real_net as exp08  # noqa: E402

NS = {"i": "http://webstds.ipc.org/2581"}
RESOLUTION_MM = 1e-6   # 1 nm: KiCad's grid and the export's precision at --precision 6
SAME_MESH_REL = 1e-9   # identical geometry must give the same drops
GATE5_ABS_MV, GATE5_REL = 0.010, 0.01   # release gate 5: 10 uV + 1 % of the drop


def _tag(element):
    return element.tag.rsplit("}", 1)[-1]


def _xy(element, x="x", y="y"):
    """Board coordinates in mm. The export's Y axis points the other way from KiCad's."""
    return float(element.get(x)), -float(element.get(y))


def _primitive_polygon(shape, centre, rotation):
    kind = _tag(shape)
    if rotation % 90:
        # The direction of rotation in the export has only been checked for multiples of 90,
        # where it cannot matter for these shapes.
        raise ValueError(f"pad rotation {rotation} is not a multiple of 90 degrees")
    if kind == "Circle":
        poly = Point(centre).buffer(float(shape.get("diameter")) / 2, 32)
    elif kind in {"RectCenter", "RectRound"}:
        w, h = float(shape.get("width")), float(shape.get("height"))
        r = float(shape.get("radius")) if kind == "RectRound" else 0.0
        if kind == "RectRound" and not all(shape.get(k) == "true" for k in
                                           ("upperRight", "upperLeft", "lowerRight", "lowerLeft")):
            raise ValueError("rounded rectangle with square corners is not supported")
        poly = box(centre[0] - w / 2 + r, centre[1] - h / 2 + r, centre[0] + w / 2 - r, centre[1] + h / 2 - r)
        if r:
            poly = poly.buffer(r, 8)
    else:
        raise ValueError(f"pad primitive {kind} is not supported")
    return affinity.rotate(poly, -rotation, origin=centre)


def _contour_polygon(contour):
    def ring(element):
        points = []
        for step in element:
            if _tag(step) not in {"PolyBegin", "PolyStepSegment"}:
                raise ValueError(f"contour step {_tag(step)} is not supported")
            points.append(_xy(step))
        return points
    outer = contour.find("i:Polygon", NS)
    return make_valid(Polygon(ring(outer), [ring(c) for c in contour.findall("i:Cutout", NS)]))


def _validate_content(group, drill=False):
    """Reject unhandled geometry, including transforms on feature containers."""
    children = {
        "Set": {"Hole"} if drill else {"Features", "Pad"},
        "Features": {"UserSpecial"}, "UserSpecial": {"Line", "Contour"},
        "Line": {"LineDesc"}, "LineDesc": set(),
        "Contour": {"Polygon", "Cutout"},
        "Polygon": {"PolyBegin", "PolyStepSegment"},
        "Cutout": {"PolyBegin", "PolyStepSegment"},
        "PolyBegin": set(), "PolyStepSegment": set(),
        "Pad": {"Location", "StandardPrimitiveRef", "PinRef", "Xform"},
        "Location": set(), "StandardPrimitiveRef": set(), "PinRef": set(),
        "Xform": set(), "Hole": set(),
    }
    for parent in group.iter():
        for child in parent:
            if _tag(child) not in children.get(_tag(parent), set()):
                if _tag(child) == "UserPrimitiveRef" and _tag(parent) == "Pad":
                    raise ValueError("custom pad shapes are not supported")
                raise ValueError(f"{_tag(child)} in {_tag(parent)} is not supported")
        if _tag(parent) == "Xform" and (
            set(parent.attrib) - {"rotation", "mirror"}
            or parent.get("mirror", "false") not in {"false", "true"}
        ):
            raise ValueError("unsupported pad transform")
        if _tag(parent) == "LineDesc" and parent.get("lineEnd", "ROUND") != "ROUND":
            raise ValueError("non-round track ends are not supported")


def read_net_ipc2581(path, net):
    """The board description exp08_real_net.read_net returns, taken from an IPC-2581 export.

    Differences from read_net: pad "type" is empty and "relief" is empty, because the export
    has neither.
    """
    root = ET.parse(path).getroot()
    if root.tag != "{%s}IPC-2581" % NS["i"] or root.get("revision") != "C":
        raise ValueError("expected an IPC-2581 revision C file")
    header = root.find(".//i:CadHeader", NS)
    if header is None or header.get("units") != "MILLIMETER":
        raise ValueError("expected MILLIMETER units")

    function = {l.get("name"): l.get("layerFunction") for l in root.findall(".//i:CadData/i:Layer", NS)}
    spans = {l.get("name"): (l.find("i:Span", NS).get("fromLayer"), l.find("i:Span", NS).get("toLayer"))
             for l in root.findall(".//i:CadData/i:Layer", NS) if l.find("i:Span", NS) is not None}
    stack = sorted(root.findall(".//i:StackupLayer", NS), key=lambda s: int(s.get("sequence")))
    copper = [s.get("layerOrGroupRef") for s in stack if function.get(s.get("layerOrGroupRef")) == "CONDUCTOR"]
    if len(copper) < 2 or len(set(copper)) != len(copper):
        raise ValueError("expected an explicit stackup with distinct copper layers")
    # Layer centres along the stack, from the first copper layer's top face (as read_net).
    z, centres, started = 0.0, {}, False
    for s in stack:
        name, kind = s.get("layerOrGroupRef"), function.get(s.get("layerOrGroupRef"), "")
        is_cu = kind == "CONDUCTOR"
        if not (is_cu or kind.startswith("DIEL")) or (not started and not is_cu):
            continue
        started, t = True, float(s.get("thickness"))
        if is_cu:
            centres[name] = (z + t / 2, t)
        z += t

    standard = {e.get("id"): e[0] for e in root.findall(".//i:DictionaryStandard/i:EntryStandard", NS)}
    tracks, zones, via_pads, pin_pads, via_holes, pad_holes = [], {}, {}, {}, [], {}
    for feature in root.findall(".//i:LayerFeature", NS):
        layer = feature.get("layerRef")
        for group in feature.findall("i:Set", NS):
            if group.get("net") != net:
                continue
            if layer in copper:
                if group.find(".//i:Arc", NS) is not None:
                    raise ValueError("track arcs are not supported")
                _validate_content(group)
                for line in group.findall(".//i:Line", NS):
                    tracks.append((layer, _xy(line, "startX", "startY"), _xy(line, "endX", "endY"),
                                   float(line.find("i:LineDesc", NS).get("lineWidth"))))
                for contour in group.findall(".//i:Contour", NS):
                    zones.setdefault(layer, []).append(_contour_polygon(contour))
                for pad in group.findall("i:Pad", NS):
                    ref = pad.find("i:StandardPrimitiveRef", NS)
                    if ref is None or ref.get("id") not in standard:
                        raise ValueError("custom pad shapes are not supported")
                    xform = pad.find("i:Xform", NS)
                    if xform is not None and xform.get("mirror") == "true":
                        raise ValueError("mirrored pads are not supported")
                    centre = _xy(pad.find("i:Location", NS))
                    poly = _primitive_polygon(standard[ref.get("id")], centre,
                                              float(xform.get("rotation", 0)) if xform is not None else 0.0)
                    pin = pad.find("i:PinRef", NS)
                    if pin is None:
                        if _tag(standard[ref.get("id")]) != "Circle":
                            raise ValueError("via pad is not circular")
                        by_layer = via_pads.setdefault(centre, {})
                        if layer in by_layer:
                            raise ValueError(f"duplicate via pad at {centre} on {layer}")
                        by_layer[layer] = float(standard[ref.get("id")].get("diameter"))
                    else:
                        entry = pin_pads.setdefault((pin.get("componentRef"), pin.get("pin")),
                                                    dict(centre=centre, poly=poly, layers=[]))
                        if entry["centre"] != centre or not entry["poly"].equals(poly):
                            raise ValueError(f"pad {pin.get('componentRef')}.{pin.get('pin')} differs between layers")
                        if layer in entry["layers"]:
                            raise ValueError("duplicate pin pad on the same layer")
                        entry["layers"].append(layer)
            elif function.get(layer) == "DRILL":
                _validate_content(group, drill=True)
                for hole in group.findall("i:Hole", NS):
                    status = hole.get("platingStatus")
                    if status not in {"VIA", "PLATED"}:
                        raise ValueError(f"hole plating status {status} is not supported")
                    if spans.get(layer) != (copper[0], copper[-1]):
                        raise ValueError("blind and buried vias or pad holes are not supported")
                    diameter = float(hole.get("diameter"))
                    if not math.isfinite(diameter) or diameter <= 0:
                        raise ValueError("hole diameter must be finite and positive")
                    if status == "VIA":
                        via_holes.append((_xy(hole), diameter, spans[layer]))
                    elif status == "PLATED":
                        if _xy(hole) in pad_holes:
                            raise ValueError("duplicate plated hole position")
                        pad_holes[_xy(hole)] = diameter
            elif function.get(layer) == "CONDUCTOR" or layer not in function:
                raise ValueError(f"copper or unknown layer {layer} is missing from the stackup")

    vias = []
    seen_vias = set()
    for centre, drill, span in via_holes:
        if centre in seen_vias:
            raise ValueError("duplicate via hole position")
        seen_vias.add(centre)
        by_layer = via_pads.get(centre, {})
        sizes = set(by_layer.values())
        if not sizes or len(sizes) != 1:
            raise ValueError(f"via at {centre} has {sorted(sizes) if sizes else 'no'} pad sizes")
        if set(by_layer) != set(copper):
            raise ValueError(f"via at {centre} lacks pad copper on some layers")
        vias.append(dict(c=centre, size=next(iter(sizes)), drill=drill, layers=list(span)))
    if set(via_pads) != seen_vias:
        raise ValueError("via pad has no matching hole")
    if set(pad_holes) - {p["centre"] for p in pin_pads.values()}:
        raise ValueError("plated hole has no matching pin pad at its position")
    pads = []
    for (ref, pin), p in pin_pads.items():
        drill = pad_holes.get(p["centre"])
        if drill is None and len(p["layers"]) != 1:
            raise ValueError("multilayer pin pad has no matching plated hole at its position")
        if drill is not None and set(p["layers"]) != set(copper):
            raise ValueError("through-hole pin pad lacks copper on some layers")
        pads.append(dict(ref=ref, pin=pin, kind="thru_hole" if drill else "smd",
                         layers=[l for l in copper if l in p["layers"]], centre=p["centre"], poly=p["poly"],
                         drill=drill, type=""))
    return dict(copper=copper, centres=centres, tracks=tracks, vias=vias, pads=pads, zones=zones, relief={})


# ------------------------------------------------------------------ comparison
def _union_difference(a, b):
    """Area in mm2 that is in one of the two polygon lists but not in the other."""
    ua = unary_union(a) if a else Polygon()
    ub = unary_union(b) if b else Polygon()
    return float(ua.symmetric_difference(ub).area), float(ua.area)


def compare_geometry(direct, export):
    """Item-by-item differences between the two board descriptions. Lengths in mm."""
    out = dict(copper_layers_equal=direct["copper"] == export["copper"])
    out["stackup_max_diff_mm"] = max(
        (abs(a - b) for l in direct["copper"] for a, b in zip(direct["centres"][l], export["centres"].get(l, (math.inf,) * 2))),
        default=0.0)

    def track_key(t):
        return (t[0], *sorted([tuple(round(v, 6) for v in t[1]), tuple(round(v, 6) for v in t[2])]), round(t[3], 6))
    dt, et = sorted(map(track_key, direct["tracks"])), sorted(map(track_key, export["tracks"]))
    out["tracks"] = dict(direct=len(dt), export=len(et), equal=dt == et)

    def via_key(v):
        return (round(v["c"][0], 6), round(v["c"][1], 6), round(v["drill"], 6), round(v["size"], 6), tuple(v["layers"]))
    dv, ev = sorted(map(via_key, direct["vias"])), sorted(map(via_key, export["vias"]))
    out["vias"] = dict(direct=len(dv), export=len(ev), equal=dv == ev)

    dp = {p["ref"] + "." + p["pin"]: p for p in direct["pads"]}
    ep = {p["ref"] + "." + p["pin"]: p for p in export["pads"]}
    worst_shift, worst_edge, worst_centre, mismatched = 0.0, 0.0, 0.0, []
    for name in sorted(set(dp) & set(ep)):
        a, b = dp[name], ep[name]
        worst_centre = max(worst_centre, math.hypot(a["centre"][0] - b["centre"][0], a["centre"][1] - b["centre"][1]))
        # Outline difference as an equivalent edge shift: area between the outlines over the perimeter.
        worst_shift = max(worst_shift, a["poly"].symmetric_difference(b["poly"]).area / a["poly"].length)
        worst_edge = max(worst_edge, a["poly"].boundary.hausdorff_distance(b["poly"].boundary))
        same_drill = (a["drill"] is None) == (b["drill"] is None) and (a["drill"] is None or abs(a["drill"] - b["drill"]) < 1e-6)
        if a["kind"] != b["kind"] or a["layers"] != b["layers"] or not same_drill:
            mismatched.append(name)
    out["pads"] = dict(direct=len(dp), export=len(ep), only_direct=sorted(set(dp) - set(ep)),
                       only_export=sorted(set(ep) - set(dp)), kind_layer_or_drill_mismatch=mismatched,
                       max_centre_diff_mm=worst_centre, max_outline_shift_mm=worst_shift,
                       max_outline_distance_mm=worst_edge)
    out["zones"] = {}
    for layer in sorted(set(direct["zones"]) | set(export["zones"])):
        diff, area = _union_difference(direct["zones"].get(layer, []), export["zones"].get(layer, []))
        out["zones"][layer] = dict(direct=len(direct["zones"].get(layer, [])), export=len(export["zones"].get(layer, [])),
                                   area_mm2=area, difference_mm2=diff)
    out["equal"] = bool(
        out["copper_layers_equal"] and out["stackup_max_diff_mm"] < 1e-9 and out["tracks"]["equal"] and out["vias"]["equal"]
        and not out["pads"]["only_direct"] and not out["pads"]["only_export"] and not mismatched
        and worst_centre < RESOLUTION_MM and worst_edge < RESOLUTION_MM
        and all(z["difference_mm2"] < 1e-9 for z in out["zones"].values()))
    return out


def solve(board, sources, loads, workdir, h, sides, growth, rho, plating, gmsh):
    """Drop in mV at every load pin, per source, for `current` drawn at each load pin."""
    layers = exp08.build_layers(board, plating, rim_segments=sides)
    meshes = {n: exp08.mesh_layer(n, layers[n], board["pads"], str(workdir), h, gmsh, growth=growth)
              for n in board["copper"] if layers[n]["polys"]}
    model = exp08.assemble(board, layers, meshes, rho, plating)
    result = dict(nodes={n: int(len(m[0])) for n, m in meshes.items()}, sources={})
    for name, pins in sources.items():
        ids, Z, unreachable, _ = exp08.transfer_matrix(model, pins, loads)
        if unreachable or not ids:
            raise ValueError(f"source {name}: unreachable or empty loads: {unreachable}")
        result["sources"][name] = dict(loads=ids, unreachable=unreachable, Z=Z)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("board", type=Path, help="saved .kicad_pcb file")
    parser.add_argument("export", type=Path, help="IPC-2581 C export of the same board")
    parser.add_argument("--net", required=True)
    parser.add_argument("--source", action="append", required=True, metavar="NAME=PIN,PIN")
    parser.add_argument("--load", required=True, help="comma-separated load pins")
    parser.add_argument("--load-current", type=float, default=0.1, help="amperes per load pin")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--h", type=float, default=0.5)
    parser.add_argument("--sides", type=int, default=96)
    parser.add_argument("--growth", type=float, default=0.15)
    parser.add_argument("--rho", type=float, default=1.724e-8)
    parser.add_argument("--plating", type=float, default=0.025, help="mm")
    parser.add_argument("--gmsh", default="gmsh")
    args = parser.parse_args()

    sources = {s.split("=")[0]: s.split("=")[1].split(",") for s in args.source}
    loads = args.load.split(",")
    direct = exp08.read_net(str(args.board), args.net)
    export = read_net_ipc2581(args.export, args.net)
    geometry = compare_geometry(direct, export)

    args.out.mkdir(parents=True, exist_ok=True)
    cases = {"direct": direct, "export_with_relief": dict(export, relief=direct["relief"]), "export": export}
    solved = {}
    for name, board in cases.items():
        (args.out / name).mkdir(exist_ok=True)
        solved[name] = solve(board, sources, loads, args.out / name, args.h, args.sides, args.growth,
                             args.rho, args.plating, args.gmsh)

    version = subprocess.run([args.gmsh, "--version"], check=True, capture_output=True, text=True, timeout=30)
    report = dict(net=args.net, mesh=dict(h_mm=args.h, hole_sides=args.sides, growth=args.growth), rho_ohm_m=args.rho,
                  board_sha256=hashlib.sha256(args.board.read_bytes()).hexdigest(),
                  export_sha256=hashlib.sha256(args.export.read_bytes()).hexdigest(),
                  gmsh_version=(version.stdout + version.stderr).strip(),
                  plating_mm=args.plating, load_current_A=args.load_current, relief_from_board_file=direct["relief"],
                  geometry=geometry, nodes={k: v["nodes"] for k, v in solved.items()}, sources={})
    same_reader, missing_relief = True, True
    for source in sources:
        ref = solved["direct"]["sources"][source]
        drop_ref = ref["Z"] @ np.full(len(ref["loads"]), args.load_current) * 1e3
        entry = dict(loads=ref["loads"], unreachable=ref["unreachable"], direct_drop_mV=drop_ref.tolist())
        for name in ("export_with_relief", "export"):
            other = solved[name]["sources"][source]
            if other["loads"] != ref["loads"]:
                raise ValueError(f"{name}: reachable load pins differ for source {source}")
            drop = other["Z"] @ np.full(len(other["loads"]), args.load_current) * 1e3
            rel = np.abs(drop / drop_ref - 1)
            entry[name] = dict(drop_mV=drop.tolist(), max_rel_diff=float(rel.max()),
                               max_abs_diff_uV=float(np.abs(drop - drop_ref).max() * 1e3),
                               max_rel_diff_Z=float(np.abs(other["Z"] - ref["Z"]).max() / ref["Z"].max()))
            if name == "export_with_relief":
                same_reader &= bool(rel.max() <= SAME_MESH_REL)
            else:
                missing_relief &= bool(np.all(np.abs(drop - drop_ref) <= GATE5_ABS_MV + GATE5_REL * np.abs(drop_ref)))
        report["sources"][source] = entry
    report.update(gates=dict(geometry=geometry["equal"], same_reader_result=same_reader, missing_relief_gap=missing_relief),
                  passed=bool(geometry["equal"] and same_reader and missing_relief))
    (args.out / "ipc2581_vs_direct.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")

    g = geometry
    print(f"geometry equal: {g['equal']}")
    print(f"  tracks {g['tracks']['export']}/{g['tracks']['direct']} equal {g['tracks']['equal']}; "
          f"vias {g['vias']['export']}/{g['vias']['direct']} equal {g['vias']['equal']}; "
          f"pads {g['pads']['export']}/{g['pads']['direct']}, outline shift {g['pads']['max_outline_shift_mm']:.2e} mm, "
          f"centre diff {g['pads']['max_centre_diff_mm']:.2e} mm")
    for layer, zone in g["zones"].items():
        print(f"  fill {layer}: {zone['export']}/{zone['direct']} polygons, {zone['area_mm2']:.3f} mm2, difference {zone['difference_mm2']:.2e} mm2")
    for source, entry in report["sources"].items():
        print(f"{source}: direct max drop {max(entry['direct_drop_mV']):.4f} mV")
        for name in ("export_with_relief", "export"):
            e = entry[name]
            print(f"  {name:19} max drop {max(e['drop_mV']):.4f} mV, largest difference {e['max_abs_diff_uV']:.3f} uV ({e['max_rel_diff']:.2e})")
    print("gates:", ", ".join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in report["gates"].items()))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

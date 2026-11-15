"""Compare a padne solve of one net with the PDNLab exp08 result for the same case.

Run (after exp08_analyze_3v3.py has written EXP08_DIR and padne has exported its solution):

    uv run python experiments/exp07_padne_compare.py BOARD.kicad_pcb EXP08_DIR PADNE_VTU_DIR \
        [PADNE_VTU_DIR ...] --output comparison.json

PADNE_VTU_DIR is the directory written by `padne paraview SOLUTION DIR` (one .vtu per layer).
Several directories (different padne mesh sizes) are compared one by one and against each other.

Two comparisons are reported for every load pin:

* raw:        drop from the source pads to the load pad, as each tool reports it;
* referenced: the same drop minus the mean drop on a ring around the feed vias on the
              reference layer. This removes whatever happens between the source pad and its
              first via, where the two tools use different terminal models (padne connects a
              terminal at the pad centre point, PDNLab ties the whole pad to one voltage).

The gate is release 0.1 gate 6 with padne as the reference:
    abs(difference) <= 50 uV + 5 % x abs(padne drop).
This script does not solve anything and does not run padne. Exit code 1 if any referenced
comparison fails the gate; the raw comparison is reported but does not set the exit code.
"""

import argparse
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import meshio
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exp08_real_net as exp08  # noqa: E402

GATE_ABS_MV = 0.05
GATE_REL = 0.05
PLATING_MM = 0.025
RING_WIDTH_MM = 0.15
RING_WIDTHS_MM = (0.10, 0.15, 0.30)   # sensitivity of the reference to the ring choice
PAD_TOLERANCE_MM = 1e-6


def read_padne_vtu(path):
    """Return (points, voltage) of every piece in a padne ParaView export.

    padne writes x, -y, 0 in mm; the sign of y is restored here so points are in KiCad
    board coordinates. A layer file holds one <Piece> per connected copper region.
    """
    points, voltage = [], []
    for piece in ET.parse(path).getroot().iter("Piece"):
        v = np.array(piece.find("PointData/DataArray[@Name='voltage']").text.split(), float)
        xyz = np.array(piece.find("Points/DataArray").text.split(), float).reshape(-1, 3)
        if len(v) != len(xyz):
            raise ValueError(f"{path}: {len(v)} voltages for {len(xyz)} points")
        points.append(np.c_[xyz[:, 0], -xyz[:, 1]])
        voltage.append(v)
    if not points:
        raise ValueError(f"{path}: no mesh pieces")
    return np.vstack(points), np.concatenate(voltage)


def vertex_voltage(points, voltage, centre, tolerance=PAD_TOLERANCE_MM):
    """Voltage of the mesh vertex at `centre`. padne seeds a vertex at every connected pad
    centre, so a missing vertex means the pad was not a terminal in that solve."""
    d = np.hypot(points[:, 0] - centre[0], points[:, 1] - centre[1])
    i = int(d.argmin())
    if d[i] > tolerance:
        raise ValueError(f"no mesh vertex at ({centre[0]:.4f}, {centre[1]:.4f}); nearest is {d[i]:.3g} mm away")
    return float(voltage[i])


def ring_mean(points, values, centre, r_in, r_out):
    """Mean of `values` over the nodes with r_in <= distance from centre <= r_out, and their count."""
    d = np.hypot(points[:, 0] - centre[0], points[:, 1] - centre[1])
    keep = (d >= r_in) & (d <= r_out)
    if not keep.any():
        raise ValueError(f"no nodes between {r_in} and {r_out} mm of ({centre[0]:.4f}, {centre[1]:.4f})")
    return float(values[keep].mean()), int(keep.sum())


def gate(reference_mv, other_mv):
    """Release 0.1 gate 6, measured against the reference tool's drop. Values in mV."""
    return bool(abs(other_mv - reference_mv) <= GATE_ABS_MV + GATE_REL * abs(reference_mv))


def feed_vias(board, source_pins):
    """For every source pin, the nearest via of the net: the via that feeds it."""
    pads = {p["ref"] + "." + p["pin"]: p for p in board["pads"]}
    out = {}
    for pin in source_pins:
        c = pads[pin]["centre"]
        out[pin] = min(board["vias"], key=lambda v: np.hypot(v["c"][0] - c[0], v["c"][1] - c[1]))
    return out


def read_pdnlab_layer(exp08_dir, layer):
    """PDNLab exp08 field of one layer: points in mm, drop in mV."""
    mesh = meshio.read(Path(exp08_dir) / f"v3v3_{layer.replace('.', '_')}.vtu")
    return mesh.points[:, :2] * 1e3, mesh.point_data["drop_V"] * 1e3


def compare(board, exp08_dir, vtu_dir, case, source_pins, source_voltage, reference_layer, net_floor_v):
    pads = {p["ref"] + "." + p["pin"]: p for p in board["pads"]}
    checks = json.loads((Path(exp08_dir) / "checks.json").read_text())[case]
    layers = sorted({pads[p]["layers"][0] for p in list(source_pins) + checks["loads"]} | {reference_layer})
    padne = {}
    for layer in layers:
        pts, v = read_padne_vtu(Path(vtu_dir) / f"{layer}.vtu")
        padne[layer] = (pts, v)
    for pin in source_pins:
        v = vertex_voltage(*padne[pads[pin]["layers"][0]], pads[pin]["centre"])
        if abs(v - source_voltage) > 1e-9:
            raise ValueError(f"source pin {pin} is at {v} V in the padne solution, expected {source_voltage} V")

    feeds = feed_vias(board, source_pins)
    pdn_pts, pdn_drop = read_pdnlab_layer(exp08_dir, reference_layer)
    ref_pts, ref_v = padne[reference_layer]
    on_net = ref_v > net_floor_v          # padne meshes every net; keep this net's copper only
    ref_pts, ref_drop = ref_pts[on_net], (source_voltage - ref_v[on_net]) * 1e3

    def references(width):
        a, b, n = [], [], []
        for via in feeds.values():
            r_in = via["drill"] / 2 + PLATING_MM
            pa, count = ring_mean(ref_pts, ref_drop, via["c"], r_in, r_in + width)
            pl, _ = ring_mean(pdn_pts, pdn_drop, via["c"], r_in, r_in + width)
            a.append(pa); b.append(pl); n.append(count)
        return float(np.mean(a)), float(np.mean(b)), n

    ref_padne, ref_pdnlab, ring_nodes = references(RING_WIDTH_MM)
    sensitivity = {f"{w:.2f}": dict(zip(["padne_mV", "pdnlab_mV", "padne_ring_nodes"], references(w)))
                   for w in RING_WIDTHS_MM}

    # Where the source-side difference sits: drop from the source pad to its feed via, on the pad's layer.
    source_side = {}
    for pin, via in feeds.items():
        layer = pads[pin]["layers"][0]
        pts, v = padne[layer]
        keep = v > net_floor_v
        r_in = via["drill"] / 2 + PLATING_MM
        pa, _ = ring_mean(pts[keep], (source_voltage - v[keep]) * 1e3, via["c"], r_in, r_in + RING_WIDTH_MM)
        pl, _ = ring_mean(*read_pdnlab_layer(exp08_dir, layer), via["c"], r_in, r_in + RING_WIDTH_MM)
        source_side[pin] = dict(layer=layer, via_xy_mm=list(via["c"]), via_drill_mm=via["drill"],
                                pad_to_via_padne_mV=pa, pad_to_via_pdnlab_mV=pl)

    pins = {}
    for pin, pdnlab in zip(checks["loads"], checks["scenario_100mA_drop_mV"]):
        drop = (source_voltage - vertex_voltage(*padne[pads[pin]["layers"][0]], pads[pin]["centre"])) * 1e3
        pins[pin] = dict(
            padne_mV=drop, pdnlab_mV=pdnlab, raw_diff_uV=(pdnlab - drop) * 1e3,
            raw_diff_pct=100 * (pdnlab / drop - 1), raw_gate=gate(drop, pdnlab),
            padne_ref_mV=drop - ref_padne, pdnlab_ref_mV=pdnlab - ref_pdnlab,
            ref_diff_uV=((pdnlab - ref_pdnlab) - (drop - ref_padne)) * 1e3,
            ref_diff_pct=100 * ((pdnlab - ref_pdnlab) / (drop - ref_padne) - 1),
            ref_gate=gate(drop - ref_padne, pdnlab - ref_pdnlab))
    return dict(vtu_dir=str(vtu_dir), vertices={k: int(len(v[1])) for k, v in padne.items()},
                reference=dict(layer=reference_layer, ring_width_mm=RING_WIDTH_MM, padne_mV=ref_padne,
                               pdnlab_mV=ref_pdnlab, padne_ring_nodes=ring_nodes, ring_sensitivity=sensitivity),
                source_side=source_side, pins=pins,
                raw_gate_passed=sum(p["raw_gate"] for p in pins.values()),
                ref_gate_passed=sum(p["ref_gate"] for p in pins.values()), pin_count=len(pins))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("board", type=Path)
    parser.add_argument("exp08_dir", type=Path)
    parser.add_argument("vtu_dirs", type=Path, nargs="+")
    parser.add_argument("--net", default="+3V3")
    parser.add_argument("--case", default="J4", help="key in EXP08_DIR/checks.json")
    parser.add_argument("--source", default="J4.6,J4.10,J4.13,J4.16", help="source pins of that case")
    parser.add_argument("--source-voltage", type=float, default=3.3, help="padne VOLTAGE value in volts")
    parser.add_argument("--reference-layer", default="In2.Cu")
    parser.add_argument("--net-floor", type=float, default=3.0,
                        help="padne vertices below this voltage belong to other nets and are ignored")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    board = exp08.read_net(str(args.board), args.net)
    runs = [compare(board, args.exp08_dir, d, args.case, args.source.split(","), args.source_voltage,
                    args.reference_layer, args.net_floor) for d in args.vtu_dirs]
    report = dict(net=args.net, case=args.case, source=args.source.split(","), gate="abs(diff) <= 50 uV + 5 % x abs(padne drop)",
                  runs=runs)
    if len(runs) > 1:      # padne against itself: first directory against each later one
        first = runs[0]["pins"]
        report["padne_mesh_change"] = [
            dict(from_dir=runs[0]["vtu_dir"], to_dir=r["vtu_dir"],
                 pins={k: dict(change_uV=(r["pins"][k]["padne_mV"] - first[k]["padne_mV"]) * 1e3,
                               change_pct=100 * (r["pins"][k]["padne_mV"] / first[k]["padne_mV"] - 1)) for k in first})
            for r in runs[1:]]
    if args.output:
        args.output.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")

    for r in runs:
        p = r["pins"].values()
        print(f"{r['vtu_dir']}: {sum(r['vertices'].values())} vertices on the layers used")
        print("  raw:        PDNLab - padne %+.2f .. %+.2f %% (%+.1f .. %+.1f uV), gate %d/%d" % (
            min(x["raw_diff_pct"] for x in p), max(x["raw_diff_pct"] for x in p),
            min(x["raw_diff_uV"] for x in p), max(x["raw_diff_uV"] for x in p), r["raw_gate_passed"], r["pin_count"]))
        print("  referenced: PDNLab - padne %+.2f .. %+.2f %% (%+.1f .. %+.1f uV), gate %d/%d" % (
            min(x["ref_diff_pct"] for x in p), max(x["ref_diff_pct"] for x in p),
            min(x["ref_diff_uV"] for x in p), max(x["ref_diff_uV"] for x in p), r["ref_gate_passed"], r["pin_count"]))
        print("  %s at the feed vias: padne %.4f mV, PDNLab %.4f mV below the source pads" % (
            r["reference"]["layer"], r["reference"]["padne_mV"], r["reference"]["pdnlab_mV"]))
    for change in report.get("padne_mesh_change", []):
        c = change["pins"].values()
        print("padne %s -> %s: %+.1f .. %+.1f uV (%+.2f .. %+.2f %%)" % (
            change["from_dir"], change["to_dir"], min(x["change_uV"] for x in c), max(x["change_uV"] for x in c),
            min(x["change_pct"] for x in c), max(x["change_pct"] for x in c)))
    raise SystemExit(0 if all(r["ref_gate_passed"] == r["pin_count"] for r in runs) else 1)


if __name__ == "__main__":
    main()

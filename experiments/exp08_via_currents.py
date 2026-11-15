"""exp08 follow-up: per-via currents, local refinement at via holes and through-hole pads, mesh convergence.

Scenario (fixed, same as exp08_analyze_3v3.py): net +3V3 of MAIN_MCU_STM32H743, supply at J4,
100 mA drawn at each IC supply pin. J1 and J5 are solved too, but only for self-Z and drop.

Run:  python experiments/exp08_via_currents.py BOARD.kicad_pcb OUTDIR
Needs shapely, a gmsh executable, and matplotlib for the map.

Writes to OUTDIR:
  v3v3_via_currents.csv      one row per via, reported level
  v3v3_via_top10.txt         top 10 by current and by current density
  v3v3_via_convergence.csv   per-via barrel current at every mesh level
  v3v3_via_checks.json       conservation, self-Z and drop per level, pass/fail gates
  v3v3_drop_map_vias.png     drop map with the via currents drawn on it

Exit code 1 if any conservation or convergence gate fails. All files are still written.

The numbers of the first exp08 run (h = 0.5 mm, no local refinement) are kept in
OUTDIR/checks_old_mesh_h0.5.json. For J4 they must still hold within 1 %. For J1 and J5 they
are superseded: those pads feed In2.Cu through thermal-relief spokes that the old mesh did
not resolve, so the old-vs-new difference is recorded, not gated.
"""

import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import exp08_real_net as e  # noqa: E402

NET = "+3V3"
SOURCES = {"J4": ["J4.6", "J4.10", "J4.13", "J4.16"], "J1": ["J1.1"], "J5": ["J5.1"]}
VIA_SOURCE = "J4"
LOAD_A = 0.1
RHO = 1.724e-8
PLATING_MM = 0.025
OLD_FILE = "checks_old_mesh_h0.5.json"
OLD_MUST_HOLD = ("J4",)  # sources whose first-run numbers are still required to hold
# (name, global h in mm, sides per via hole or None for the original mesh, growth in mm per mm)
LEVELS = [
    ("old h=0.5", 0.5, None, None),
    ("h=0.5 n=24", 0.5, 24, 0.3),
    ("h=0.5 n=48", 0.5, 48, 0.2),
    ("h=0.5 n=96", 0.5, 96, 0.15),
    ("h=0.25 n=96", 0.25, 96, 0.15),
    ("h=0.125 n=96", 0.125, 96, 0.15),
]
REPORTED = "h=0.5 n=96"
REL_GATE = 0.01          # "about 1 %"
CURRENT_FLOOR = 1e-3     # of the source current, added to the per-via gate


def solve_level(board, loads, h, sides, growth, workdir, gmsh):
    layers = e.build_layers(board, PLATING_MM, rim_segments=sides)
    meshes = {n: e.mesh_layer(n, layers[n], board["pads"], workdir, h, gmsh, growth=growth)
              for n in board["copper"] if layers[n]["polys"]}
    model = e.assemble(board, layers, meshes, RHO, PLATING_MM)
    out = dict(nodes={n: len(m[0]) for n, m in meshes.items()}, gmsh=next(iter(meshes.values()))[2], sources={})
    keep = None
    for name, pins in SOURCES.items():
        ids, Z, unreachable, ctx = e.transfer_matrix(model, pins, loads)
        if unreachable or ids != loads:
            raise RuntimeError(f"source {name}: loads solved {ids}, unreachable {unreachable}")
        drop, balance = e.solve_scenario(model, ctx, {k: LOAD_A for k in ids})
        out["sources"][name] = dict(selfZ_mohm=(np.diag(Z) * 1e3).tolist(),
                                    pin_drop_mV=[float(drop[model["pin_red"][k][0]] * 1e3) for k in ids],
                                    max_drop_mV=float(drop.max() * 1e3), balance=balance)
        if name == VIA_SOURCE:
            vias = e.via_currents(board, model, drop)
            out["conservation"] = e.via_conservation(model, ctx, drop, vias)
            keep = dict(vias=vias, meshes=meshes, model=model, drop=drop)
    return out, keep


def rel(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return float(np.max(np.abs(a / b - 1)))


def gates(results, stored):
    """Pass/fail list, and the recorded differences for superseded old numbers.

    Nothing in the first list is advisory: a False fails the run.
    """
    names = [n for n, *_ in LEVELS]
    refined = names[1:]
    out, superseded = [], []

    def add(name, ok, **info):
        out.append(dict(gate=name, ok=bool(ok), **info))

    for n in names:
        c = results[n]["conservation"]
        add(f"conservation [{n}]", c["ok"], worst_A=max(c["max_rim_kcl_residual_A"], c["max_via_rim_sum_A"],
                                                       c["max_pin_sheet_vs_solver_A"], c["max_gap_difference_A"]),
            tolerance_A=c["tolerance_A"])
    for a, b in zip(refined, refined[1:]):
        ia = np.array([v["barrel_abs_max"] for v in results[a]["vias"]])
        ib = np.array([v["barrel_abs_max"] for v in results[b]["vias"]])
        source = abs(results[b]["conservation"]["source_current_A"])
        excess = np.abs(ia - ib) - (REL_GATE * ib + CURRENT_FLOOR * source)
        k = int(np.argmax(np.abs(ia - ib)))
        add(f"per-via current stable [{a} -> {b}]", np.all(excess <= 0), worst_via=k,
            worst_change_mA=float((ib[k] - ia[k]) * 1e3), worst_via_current_mA=float(ib[k] * 1e3))
        for s in SOURCES:
            ra, rb = results[a]["sources"][s], results[b]["sources"][s]
            add(f"self-Z stable, source {s} [{a} -> {b}]", rel(ra["selfZ_mohm"], rb["selfZ_mohm"]) <= REL_GATE,
                max_rel_change=rel(ra["selfZ_mohm"], rb["selfZ_mohm"]))
            add(f"max drop stable, source {s} [{a} -> {b}]", abs(ra["max_drop_mV"] / rb["max_drop_mV"] - 1) <= REL_GATE,
                rel_change=float(rb["max_drop_mV"] / ra["max_drop_mV"] - 1),
                from_mV=ra["max_drop_mV"], to_mV=rb["max_drop_mV"])
    old = {s: results[names[0]]["sources"][s] for s in SOURCES}
    if stored is not None:
        for s in SOURCES:
            add(f"{OLD_FILE} reproduced by the old mesh, source {s}",
                rel(stored[s]["selfZ_mohm"], old[s]["selfZ_mohm"]) <= 1e-6
                and rel(stored[s]["scenario_100mA_drop_mV"], old[s]["pin_drop_mV"]) <= 1e-6,
                max_rel_diff=max(rel(stored[s]["selfZ_mohm"], old[s]["selfZ_mohm"]),
                                 rel(stored[s]["scenario_100mA_drop_mV"], old[s]["pin_drop_mV"])))
    for s in SOURCES:
        for n in refined:
            new = results[n]["sources"][s]
            dz = rel(old[s]["selfZ_mohm"], new["selfZ_mohm"])
            dd = abs(old[s]["max_drop_mV"] / new["max_drop_mV"] - 1)
            info = dict(selfZ_max_rel_diff=dz, max_drop_rel_diff=float(dd),
                        old_max_drop_mV=old[s]["max_drop_mV"], new_max_drop_mV=new["max_drop_mV"])
            if s in OLD_MUST_HOLD:
                add(f"old h=0.5 numbers hold within {REL_GATE:.0%}, source {s} [vs {n}]", max(dz, dd) <= REL_GATE, **info)
            else:
                superseded.append(dict(source=s, level=n, holds_within_1pct=bool(max(dz, dd) <= REL_GATE), **info))
    return out, superseded


def write_csv(path, vias, names):
    pairs = sorted({(b["upper"], b["lower"]) for v in vias for b in v["barrel"]},
                   key=lambda p: (names.index(p[0]), names.index(p[1])))
    head = (["via", "x_mm", "y_mm", "drill_mm", "pad_mm", "layers", "I_barrel_A"]
            + [f"I_barrel_{a}_to_{b}_A" for a, b in pairs] + [f"I_rim_{n}_A" for n in names]
            + ["kcl_residual_A", "plating_area_mm2", "J_barrel_A_per_mm2"]
            + [f"J_edge_avg_{n}_A_per_mm2" for n in names] + [f"J_edge_peak_{n}_A_per_mm2" for n in names])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(head)
        for v in vias:
            seg = {(b["upper"], b["lower"]): b["amp"] for b in v["barrel"]}
            main = max(seg.values(), key=abs) if seg else ""
            w.writerow([v["via"], f"{v['x']:.4f}", f"{v['y']:.4f}", v["drill"], v["size"], "|".join(v["layers"]),
                        f"{main:.9g}" if seg else ""]
                       + [f"{seg[p]:.9g}" if p in seg else "" for p in pairs]
                       + [f"{v['rim'][n]:.9g}" if n in v["rim"] else "" for n in names]
                       + [f"{v['kcl_residual']:.3g}", f"{v['plating_area_mm2']:.6g}", f"{v['j_barrel']:.6g}"]
                       + [f"{v['j_edge_avg'][n]:.6g}" if n in v["j_edge_avg"] else "" for n in names]
                       + [f"{v['j_edge_peak'][n]:.6g}" if n in v["j_edge_peak"] else "" for n in names])


def top10_text(vias, level):
    def peak_edge(v):
        n = max(v["j_edge_peak"], key=v["j_edge_peak"].get)
        return v["j_edge_peak"][n], n

    def avg_edge(v):
        n = max(v["j_edge_avg"], key=v["j_edge_avg"].get)
        return v["j_edge_avg"][n], n

    def row(v):
        (pk, pl), (av, al) = peak_edge(v), avg_edge(v)
        return (f"{v['via']:>3}  {v['x']:8.3f} {v['y']:8.3f}  {v['drill']:.2f}  {'|'.join(v['layers']):<17}"
                f" {v['barrel_abs_max'] * 1e3:8.2f}  {v['j_barrel']:7.2f}  {av:7.2f} {al:<7} {pk:7.2f} {pl}")

    head = ("via      x_mm     y_mm  drill layers            I_barrel   J_barr  J_edge_avg      J_edge_peak\n"
            "                          mm                          mA    A/mm2    A/mm2 layer     A/mm2 layer")
    lines = [f"+3V3, supply at {VIA_SOURCE}, {LOAD_A * 1e3:g} mA at each IC supply pin. Mesh level: {level}.",
             "I_barrel: largest barrel-segment current of the via. J_barr: that current over the plating",
             f"cross-section ({PLATING_MM * 1e3:g} um wall). J_edge: current density where the layer meets the hole, average",
             "over perimeter x layer thickness and local peak; the layer with the highest value is named.", ""]
    for title, key in [("Top 10 by barrel current", lambda v: v["barrel_abs_max"]),
                       ("Top 10 by barrel current density (plating cross-section)", lambda v: v["j_barrel"]),
                       ("Top 10 by peak hole-edge current density", lambda v: peak_edge(v)[0])]:
        ranked = sorted(vias, key=lambda v: (-key(v), v["via"]))
        cut = key(ranked[9]) if len(ranked) > 9 else None
        ties = [v["via"] for v in ranked[10:] if cut is not None and np.isclose(key(v), cut, rtol=1e-6, atol=0)]
        lines += [title, head] + [row(v) for v in ranked[:10]]
        if ties:
            lines.append(f"  tie at rank 10: vias {ties} have the same value and are cut only by index")
        lines.append("")
    return "\n".join(lines)


def draw(path, board, loads, keep, level):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.tri as mtri

    meshes, model, drop, vias = keep["meshes"], keep["model"], keep["drop"], keep["vias"]
    names = list(meshes)
    biggest = max(v["barrel_abs_max"] for v in vias)
    xy = np.vstack([m[0] for m in meshes.values()])
    (x0, y0), (x1, y1) = xy.min(axis=0) - 1.5, xy.max(axis=0) + 1.5
    label_x, step = x1 + 1.0, 1.9  # labels sit in a column right of the board, mm
    fig, axs = plt.subplots(1, len(names), figsize=(5.1 * len(names), 5.5), sharex=True, sharey=True)
    vmax = float(drop.max() * 1e3)
    for ax, n in zip(np.atleast_1d(axs), names):
        pts, tri, _ = meshes[n]
        off = model["offsets"][n]
        t = mtri.Triangulation(pts[:, 0], -pts[:, 1], tri)
        cs = ax.tripcolor(t, drop[model["node_map"][off:off + len(pts)]] * 1e3, shading="gouraud",
                          cmap="viridis", vmin=0, vmax=vmax)
        for p in board["pads"]:
            if n in p["layers"]:
                lab = p["ref"] + "." + p["pin"]
                if lab in loads:
                    ax.plot(p["centre"][0], -p["centre"][1], "s", ms=4, mfc="none", mec="red", mew=1)
                if lab in SOURCES[VIA_SOURCE]:
                    ax.plot(p["centre"][0], -p["centre"][1], "k^", ms=7)
        here = [v for v in vias if n in v["layers"]]
        for v in here:
            if v["barrel_abs_max"] < 1e-6:
                ax.plot(v["x"], -v["y"], "x", ms=4, color="0.35", mew=0.8)
        live = sorted((v for v in here if v["barrel_abs_max"] >= 1e-6), key=lambda v: v["y"])
        at = None
        for v in live:  # top of the board first; keep the labels at least one step apart
            amp = v["barrel_abs_max"]
            size = 18 + 190 * amp / biggest
            ax.scatter([v["x"]], [-v["y"]], s=size, facecolors="none", edgecolors="white", linewidths=2.4, zorder=4)
            ax.scatter([v["x"]], [-v["y"]], s=size, facecolors="none", edgecolors="black", linewidths=1.2, zorder=5)
            at = -v["y"] if at is None else min(-v["y"], at - step)
            ax.plot([v["x"], label_x - 0.3], [-v["y"], at], color="0.25", lw=0.5, zorder=3)
            ax.text(label_x, at, f"v{v['via']}  {amp * 1e3:.0f} mA", fontsize=7.5, va="center", ha="left", color="black")
        ax.set_title(f"{n} (+3V3 copper only)", fontsize=10)
        ax.set_aspect("equal")
        ax.set_xlim(x0, x1 + 13.5)
        ax.set_ylim(-y1, -y0)
        ax.set_xlabel("x [mm]")
    np.atleast_1d(axs)[0].set_ylabel("-y [mm]")
    fig.colorbar(cs, ax=axs, label=f"voltage drop from {VIA_SOURCE} [mV]", shrink=0.7, pad=0.02)
    fig.suptitle(f"+3V3 drop and via currents, supply at {VIA_SOURCE} (black triangles), {LOAD_A * 1e3:g} mA at each of "
                 f"{len(loads)} IC supply pins (red squares). Mesh: {level}.\nCircles: vias in contact with this layer, "
                 "area grows with barrel current (largest barrel segment of the via). Grey crosses: vias carrying no current.",
                 fontsize=10, y=1.0)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--gmsh", default="gmsh")
    ap.add_argument("--mesh-dir", type=Path, help="keep .geo/.msh files here (default: temporary directory)")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    board = e.read_net(a.board, NET)
    loads = [p["ref"] + "." + p["pin"] for p in board["pads"] if p["type"] == "power_in"]
    results, kept = {}, None
    with tempfile.TemporaryDirectory() as tmp:
        work = a.mesh_dir or tmp
        Path(work).mkdir(parents=True, exist_ok=True)
        for name, h, sides, growth in LEVELS:
            res, keep = solve_level(board, loads, h, sides, growth, work, a.gmsh)
            res["vias"] = keep["vias"]
            results[name] = res
            if name == REPORTED:
                kept = keep
            print(f"{name:<13} nodes {sum(res['nodes'].values()):>7}  max drop mV "
                  + "  ".join(f"{k} {v['max_drop_mV']:.4f}" for k, v in res["sources"].items())
                  + f"  conservation {'ok' if res['conservation']['ok'] else 'FAIL'}", flush=True)

    names = kept["model"]["names"]
    vias = kept["vias"]
    write_csv(a.out / "v3v3_via_currents.csv", vias, names)
    text = top10_text(vias, REPORTED)
    (a.out / "v3v3_via_top10.txt").write_text(text + "\n", encoding="utf-8")
    with open(a.out / "v3v3_via_convergence.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["via", "layers"] + [f"I_barrel_mA [{n}]" for n, *_ in LEVELS]
                   + [f"J_edge_peak_A_per_mm2 [{n}]" for n, *_ in LEVELS])
        for i, v in enumerate(vias):
            w.writerow([v["via"], "|".join(v["layers"])]
                       + [f"{results[n]['vias'][i]['barrel_abs_max'] * 1e3:.4f}" for n, *_ in LEVELS]
                       + [f"{max(results[n]['vias'][i]['j_edge_peak'].values()):.3f}" for n, *_ in LEVELS])
    stored_path = a.out / OLD_FILE
    stored = json.loads(stored_path.read_text(encoding="utf-8")) if stored_path.is_file() else None
    gate_list, superseded = gates(results, stored)
    report = dict(board=str(a.board), net=NET, via_source=VIA_SOURCE, load_A_per_pin=LOAD_A, loads=loads,
                  rho_ohm_m=RHO, plating_um=PLATING_MM * 1e3, reported_level=REPORTED,
                  levels=[dict(name=n, h_mm=h, via_hole_sides=s, growth_mm_per_mm=g,
                               **{k: results[n][k] for k in ("nodes", "gmsh", "sources", "conservation")})
                          for n, h, s, g in LEVELS],
                  gates=gate_list, all_gates_pass=all(g["ok"] for g in gate_list),
                  superseded_old_numbers=superseded)
    (a.out / "v3v3_via_checks.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    draw(a.out / "v3v3_drop_map_vias.png", board, loads, kept, REPORTED)

    print()
    print(text)
    c = results[REPORTED]["conservation"]
    for g in c["gaps"]:
        way = "down" if g["barrels_down_A"] >= 0 else "up"
        print(f"gap {g['gap']}: via barrels carry {abs(g['barrels_down_A']):.6f} A net {way}; the solver's terminal "
              f"currents require {abs(g['solver_terminals_above_A']):.6f} A; difference {g['difference_A']:.1e} A")
    for s_ in SOURCES:
        if s_ not in OLD_MUST_HOLD:
            x = next(t for t in superseded if t["source"] == s_ and t["level"] == REPORTED)
            print(f"source {s_}: old max drop {x['old_max_drop_mV']:.4f} mV is superseded by {x['new_max_drop_mV']:.4f} mV "
                  f"({x['new_max_drop_mV'] / x['old_max_drop_mV'] - 1:+.2%}), self-Z differs by up to {x['selfZ_max_rel_diff']:.2%}")
    failed = [g for g in gate_list if not g["ok"]]
    print(f"\n{len(gate_list) - len(failed)} of {len(gate_list)} gates pass")
    for g in failed:
        print("FAIL:", json.dumps(g))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

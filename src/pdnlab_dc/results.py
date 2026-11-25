"""Write a RunResult to a folder: summary, CSV tables, VTU fields for ParaView, PNG maps.

``write_results(run, folder, title)`` writes (decided by Erekle, 2026-11-22):

* ``summary.txt``: per rail the voltage each load sees, split into the power drop and
  the GND rise; how the refinement check settled; the highest barrel currents; the
  current-density hot spots per layer (peak and averaged over a 0.25 mm disk, with the
  caveat); losses; the solver's checks; the assumptions.
* ``loads.csv``, ``pins.csv`` (each pin's share of its group), ``vias.csv`` (every
  plated hole's largest barrel current, highest first).
* Per net and layer: ``<net>_<layer>.vtu`` (voltage per node, current density per
  triangle, for ParaView) and two PNG maps, ``<net>_<layer>_voltage.png`` (drop from the
  source on power, rise above the source's GND on GND) and ``<net>_<layer>_current.png``.

Coordinates are KiCad's (mm, Y down); maps are drawn as seen from the top.
"""

import csv
import math
import re
from pathlib import Path

import matplotlib
import meshio
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt                           # noqa: E402
from matplotlib.tri import Triangulation                   # noqa: E402

from .run import AVERAGE_RADIUS_M                          # noqa: E402


def _safe(name):
    return re.sub(r"[^A-Za-z0-9+._-]", "_", name)


def _mm(point):
    return f"({point[0] * 1e3:.3f}, {point[1] * 1e3:.3f}) mm"


def _fields(run):
    for rail in run.rails:
        for solve in (rail.power, rail.gnd):
            for f in solve.fields:
                yield rail, solve, f


def write_results(run, folder, title):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written = [_summary(run, folder / "summary.txt", title)]
    written += _tables(run, folder)
    seen = set()
    for rail, solve, f in _fields(run):
        key = (f.net, f.layer_id)
        if key in seen:                     # GND shared by two rails: one rail's maps each
            stem = f"{_safe(f.net)}_{_safe(f.layer_id)}_{_safe(rail.power_net)}-rail"
        else:
            stem = f"{_safe(f.net)}_{_safe(f.layer_id)}"
        seen.add(key)
        written.append(_vtu(rail, solve, f, folder / f"{stem}.vtu"))
        written += _maps(rail, solve, f, folder, stem)
    return written


def _summary(run, path, title):
    lines = [f"PDNLab DC result: {title}", ""]
    for rail in run.rails:
        lines += [f"Rail {rail.power_net} (GND {rail.gnd_net}), source {rail.source!r} at "
                  f"{rail.volts:g} V", ""]
        lines.append(f"  {'load':16} {'amps':>8} {'sees (V)':>10} {'drop (mV)':>10} "
                     f"{'power (mV)':>11} {'GND (mV)':>9} {'drop %':>7}")
        for load in rail.loads:
            lines.append(f"  {load.name:16} {load.amps:8.3f} {load.v_seen:10.6f} "
                         f"{load.drop * 1e3:10.3f} {(rail.volts - load.v_power) * 1e3:11.3f} "
                         f"{load.v_gnd_rise * 1e3:9.3f} {100 * load.drop / rail.volts:7.3f}")
        lines += ["", "  Refinement check (voltage each load sees, per mesh scale):"]
        for scale, values in rail.history:
            lines.append(f"    scale {scale:<6g} " + "  ".join(f"{n} {v:.6f} V" for n, v in values.items()))
        for name in rail.last_change_v:
            ok = rail.last_change_v[name] <= rail.allowed_v[name]
            lines.append(f"    {name}: last change {rail.last_change_v[name] * 1e6:.2f} µV, "
                         f"allowed {rail.allowed_v[name] * 1e6:.2f} µV: "
                         f"{'settled' if ok and rail.settled else 'not settled'}")
        for scale, need, peak in rail.memory:
            lines.append(f"    memory at scale {scale:<6g} peak {peak / 1024**3:.2f} GB"
                         + (f", predicted {need / 1024**3:.2f} GB" if need else "")
                         if peak else f"    memory at scale {scale:g}: not measured here")
        if not rail.settled:
            lines.append(f"    NOT SETTLED: {rail.unsettled_reason}. These results are from "
                         f"scale {rail.scale:g}"
                         + ("" if rail.last_change_v else ", with no refinement check done") + ".")
        for solve in (rail.power, rail.gnd):
            lines += ["", f"  {solve.net} ({solve.role}): {solve.triangles} triangles, "
                          f"{solve.seconds:.1f} s"]
            loss = sum(f.loss_w for f in solve.fields)
            lines.append(f"    losses: " + ", ".join(f"{f.layer_id} {f.loss_w * 1e3:.3f} mW"
                                                  for f in solve.fields)
                         + f", barrels {solve.barrel_loss_w * 1e3:.3f} mW, total "
                         f"{(loss + solve.barrel_loss_w) * 1e3:.3f} mW")
            lines.append(f"    solver checks: {'passed' if solve.checks.get('passed') else 'FAILED'}")
            for spot in solve.hot_spots:
                lines.append(f"    {spot.layer_id}: current density peak {spot.peak_a_mm2:.2f} A/mm² "
                             f"at {_mm(spot.peak_at_m)}; averaged over a "
                             f"{AVERAGE_RADIUS_M * 1e3:g} mm disk {spot.averaged_a_mm2:.2f} A/mm² "
                             f"at {_mm(spot.averaged_at_m)}")
            for via in solve.vias[:10]:
                lines.append(f"    {via.kind} {via.drill_id} at {_mm(via.position_m)}: "
                             f"{via.current_a:.4f} A")
            if len(solve.vias) > 10:
                lines.append(f"    ... {len(solve.vias) - 10} more holes in vias.csv")
        lines.append("")
    lines += _timing_lines(run)
    lines += [
        "Notes",
        f"  Plating {run.plating_m * 1e6:g} µm; resistivity {run.resistivity_ohm_m:.4g} Ω·m "
        f"(planes), {run.barrel_resistivity_ohm_m:.4g} Ω·m (barrels); no temperature rise.",
        f"  Refinement tolerance {run.tolerance_v * 1e6:g} µV + {run.tolerance_rel * 100:g} % of "
        "each load's drop.",
        "  Pin groups are equipotential (ideal die or connector); pins.csv gives each pin's share.",
        "  A group with pins on several layers is joined between layers by 1 µΩ links",
        "  (1 µV per ampere).",
        "  Current density is constant per triangle and unbounded in theory at pad and inner",
        "  copper corners: a peak there grows with refinement and means current crowds there;",
        f"  the {AVERAGE_RADIUS_M * 1e3:g} mm average converges. Barrels: no contact resistance.",
        f"  Solved in {run.seconds:.1f} s"
        + (f", peak memory {run.peak_memory_bytes / 1024**3:.1f} GB" if run.peak_memory_bytes else "")
        + (f", limit {run.memory_limit_bytes / 1024**3:.1f} GB" if run.memory_limit_bytes else "") + ".",
        *(f"  {note}" for note in run.notes),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _stage_rows(run):
    """(rail, net, role, scale, triangles, stage, factorisation s) for every timed stage, in
    order: the stages before the rails, then each rail's rounds, power then GND. A row
    with stage None gives the LU factorisation inside the "prepare" before it."""
    for stage in run.stages:
        yield "", "", "", None, None, stage, None
    for rail in run.rails:
        for r in rail.rounds:
            for stage in r.stages:
                yield rail.power_net, r.net, r.role, r.scale, r.triangles, stage, None
            if r.factorization_s:
                yield rail.power_net, r.net, r.role, r.scale, r.triangles, None, r.factorization_s


def _gb(value):
    return "-" if value is None else f"{value / 1024**3:.2f}"


def _timing_lines(run):
    rows = list(_stage_rows(run))
    if not rows:
        return []
    lines = ["Time and memory by stage",
             "  wall and CPU time in s; memory in GB: held after the stage, this process's "
             "peak so far,",
             "  the largest Gmsh run so far. CPU well below wall: the stage waited (disk, or "
             "other programs).",
             f"    {'stage':42} {'wall':>8} {'CPU':>8} {'held':>6} {'peak':>6} {'Gmsh':>6}"]
    for rail, net, role, scale, triangles, stage, factorization in rows:
        if stage is None:
            lines.append(f"    {'  of which LU factorisation':42} {factorization:8.1f}")
            continue
        what = net if role == "power" else f"{net} ({rail})"
        name = stage.name if not net else f"{what} scale {scale:g}: {stage.name}"
        lines.append(f"    {name:42} {stage.seconds:8.1f} {stage.cpu_seconds:8.1f} "
                     f"{_gb(stage.rss_bytes):>6} {_gb(stage.peak_bytes):>6} "
                     f"{_gb(stage.child_peak_bytes):>6}")
    return lines + [""]


def _tables(run, folder):
    paths = []
    rows = [("rail", "load", "amps", "v_seen_V", "drop_mV", "power_drop_mV", "gnd_rise_mV",
             "drop_percent", "settled")]
    for rail in run.rails:
        for load in rail.loads:
            rows.append((rail.power_net, load.name, load.amps, f"{load.v_seen:.9f}",
                         f"{load.drop * 1e3:.6f}", f"{(rail.volts - load.v_power) * 1e3:.6f}",
                         f"{load.v_gnd_rise * 1e3:.6f}", f"{100 * load.drop / rail.volts:.6f}",
                         "yes" if rail.settled else "no"))
    paths.append(_csv(folder / "loads.csv", rows))
    rows = [("rail", "net", "group", "pin", "layer", "current_A", "share_percent")]
    for rail in run.rails:
        for solve in (rail.power, rail.gnd):
            totals = {}
            for p in solve.pins:
                totals[p.group] = totals.get(p.group, 0.0) + p.current_a
            for p in solve.pins:
                share = 100 * p.current_a / totals[p.group] if totals[p.group] else math.nan
                rows.append((rail.power_net, p.net, p.group, p.pin, p.layer_id,
                             f"{p.current_a:.9f}", f"{share:.3f}"))
    paths.append(_csv(folder / "pins.csv", rows))
    rows = [("rail", "net", "hole", "kind", "x_mm", "y_mm", "current_A")]
    for rail in run.rails:
        for solve in (rail.power, rail.gnd):
            for v in solve.vias:
                rows.append((rail.power_net, v.net, v.drill_id, v.kind, f"{v.position_m[0] * 1e3:.4f}",
                             f"{v.position_m[1] * 1e3:.4f}", f"{v.current_a:.9f}"))
    paths.append(_csv(folder / "vias.csv", rows))
    rows = [("rail", "net", "role", "scale", "triangles", "stage", "wall_s", "cpu_s",
             "held_bytes", "peak_bytes", "gmsh_peak_bytes")]
    for rail, net, role, scale, triangles, stage, factorization in _stage_rows(run):
        head = (rail, net, role, "" if scale is None else f"{scale:g}",
                "" if triangles is None else triangles)
        if stage is None:
            rows.append(head + ("factorisation (in prepare)", f"{factorization:.3f}", "", "", "", ""))
        else:
            rows.append(head + (stage.name, f"{stage.seconds:.3f}", f"{stage.cpu_seconds:.3f}",
                                *("" if v is None else v for v in
                                  (stage.rss_bytes, stage.peak_bytes, stage.child_peak_bytes))))
    paths.append(_csv(folder / "timing.csv", rows))
    return paths


def _csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)
    return path


def _shown(rail, solve, f):
    """What the voltage map shows, in mV: drop from the source, or rise above its GND."""
    if solve.role == "power":
        return (rail.volts - f.voltage_v) * 1e3, "drop from the source (mV)"
    return f.voltage_v * 1e3, "rise above the source's GND (mV)"


def _vtu(rail, solve, f, path):
    points = np.column_stack([f.nodes * 1e3, np.full(len(f.nodes), f.z_m * 1e3)])   # mm
    shown, _ = _shown(rail, solve, f)
    density = np.column_stack([f.current_density, np.zeros(len(f.triangles))])
    mesh = meshio.Mesh(points, [("triangle", f.triangles)],
                       point_data={"voltage_V": f.voltage_v, "map_mV": shown},
                       cell_data={"current_density_A_per_mm2": [density],
                                  "current_density_magnitude_A_per_mm2":
                                      [np.hypot(*f.current_density.T)]})
    meshio.write(path, mesh, file_format="vtu")
    return path


def _maps(rail, solve, f, folder, stem):
    tri = Triangulation(f.nodes[:, 0] * 1e3, f.nodes[:, 1] * 1e3, f.triangles)
    shown, label = _shown(rail, solve, f)
    magnitude = np.hypot(*f.current_density.T)
    cap = float(np.percentile(magnitude, 99.5)) if magnitude.size else 0.0
    paths = []
    for kind, values, colour, title in (
            ("voltage", shown, label, f"{f.net} on {f.layer_id}: {label}"),
            ("current", np.minimum(magnitude, cap), "current density (A/mm²)",
             f"{f.net} on {f.layer_id}: current density, scale capped at the 99.5th "
             f"percentile {cap:.2f} (peak {magnitude.max():.2f}) A/mm²")):
        fig, ax = plt.subplots(figsize=(10, 8), dpi=120)
        if kind == "voltage":
            art = ax.tripcolor(tri, values, shading="gouraud", cmap="viridis")
        else:
            art = ax.tripcolor(tri, facecolors=values, cmap="inferno", vmin=0, vmax=cap or None)
        fig.colorbar(art, ax=ax, label=colour)
        ax.set_aspect("equal")
        ax.invert_yaxis()                       # KiCad's Y points down: drawn as in KiCad
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("y (mm)")
        ax.set_title(title, fontsize=9)
        path = folder / f"{stem}_{kind}.png"
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        paths.append(path)
    return paths

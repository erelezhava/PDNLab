"""Solve a checked setup on a board: every rail's power net and its GND, with the
refinement check, and everything the results report.

``simulate(bound, board, clean)`` returns ``RunResult``. Decided by Erekle (2026-11-22,
docs/DECISIONS.md):

* Power and GND are separate networks with the same currents. On the power net the
  source's pin group is held at its voltage and each load's group draws its current; on
  GND the source's group is the 0 V reference and each load returns its current. The
  Phase 1 network only draws current at loads, so GND is solved with the loads drawing
  and its voltages negated: by linearity that is the GND rise. A load sees
  V(power at its group) − V(GND at its group).
* Pin groups are equipotential; each pin's share of its group's current is reported.
* The user does not choose element sizes: each rail is solved at scale 1, then 0.5,
  and on until the voltage every load sees changes by at most the tolerance (default
  10 µV + 1 % of its drop), at most 3 refinements; then the finer result is reported.
  Otherwise RefinementError names the load that did not settle.
* Current density (A/mm²) is constant per linear triangle and unbounded in theory at
  pad and inner copper corners: peaks are reported with the density averaged over a
  0.25 mm radius disk, which converges.
"""

import math
from dataclasses import dataclass, field, replace
from time import perf_counter

import numpy as np
from scipy.spatial import cKDTree

from .board_mesh import MeshSizes, mesh_net
from .board_network import build_net_network
from .fem import assemble_sheet
from .memory import available_bytes, current_bytes, peak_bytes

SCALES = (1.0, 0.5, 0.25, 0.125)
AVERAGE_RADIUS_M = 0.25e-3


class RefinementError(RuntimeError):
    """The result did not settle within the refinement rounds."""


@dataclass(frozen=True)
class LoadResult:
    name: str
    power_net: str
    amps: float
    v_power: float          # power net voltage at the load's group
    v_gnd_rise: float       # GND voltage at the load's group, above the source's GND
    v_seen: float           # v_power - v_gnd_rise
    drop: float             # source volts - v_seen


@dataclass(frozen=True)
class PinResult:
    net: str
    group: str
    pin: str
    layer_id: str
    current_a: float        # into the copper from the part (out of it at a load)


@dataclass(frozen=True)
class ViaResult:
    net: str
    drill_id: str
    kind: str
    position_m: tuple[float, float]
    current_a: float        # largest barrel current of the hole


@dataclass(frozen=True)
class LayerField:
    """One net on one layer: the mesh, voltages and current density."""
    net: str
    layer_id: str
    z_m: float
    nodes: np.ndarray
    triangles: np.ndarray
    voltage_v: np.ndarray           # per node: power voltage, or GND rise
    current_density: np.ndarray     # per triangle (M, 2), A/mm², volume density
    loss_w: float


@dataclass(frozen=True)
class HotSpot:
    net: str
    layer_id: str
    peak_a_mm2: float
    peak_at_m: tuple[float, float]
    averaged_a_mm2: float           # over a AVERAGE_RADIUS_M disk
    averaged_at_m: tuple[float, float]


@dataclass(frozen=True)
class NetSolve:
    net: str
    role: str                       # "power" or "gnd"
    fields: tuple[LayerField, ...]
    pins: tuple[PinResult, ...]
    vias: tuple[ViaResult, ...]
    hot_spots: tuple[HotSpot, ...]
    barrel_loss_w: float
    checks: dict
    triangles: int
    seconds: float
    group_voltage_v: dict           # group name -> its voltage (GND: rise above the source)


@dataclass(frozen=True)
class RailResult:
    power_net: str
    gnd_net: str
    source: str
    volts: float
    scale: float
    loads: tuple[LoadResult, ...]
    power: NetSolve
    gnd: NetSolve
    history: tuple                  # ((scale, {load: v_seen}), ...)
    last_change_v: dict             # load -> change at the last halving (empty: none done)
    allowed_v: dict                 # load -> tolerance at the last halving
    settled: bool = True
    unsettled_reason: str = ""      # why the next refinement was not run
    memory: tuple = ()              # ((scale, predicted bytes or None, peak bytes), ...)


@dataclass(frozen=True)
class RunResult:
    rails: tuple[RailResult, ...]
    plating_m: float
    resistivity_ohm_m: float
    barrel_resistivity_ohm_m: float
    tolerance_v: float
    tolerance_rel: float
    seconds: float
    notes: tuple[str, ...] = field(default=())
    memory_limit_bytes: int | None = None
    peak_memory_bytes: int | None = None


def _gradient(nodes, triangles, voltage):
    """Gradient of the linear voltage on each triangle, (M, 2), V/m."""
    p = nodes[triangles]
    v = voltage[triangles]
    x, y = p[..., 0], p[..., 1]
    det = (x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])
    gx = ((v[:, 1] - v[:, 0]) * (y[:, 2] - y[:, 0]) - (v[:, 2] - v[:, 0]) * (y[:, 1] - y[:, 0])) / det
    gy = ((x[:, 1] - x[:, 0]) * (v[:, 2] - v[:, 0]) - (x[:, 2] - x[:, 0]) * (v[:, 1] - v[:, 0])) / det
    return np.column_stack([gx, gy])


def _hot_spot(f):
    p = f.nodes[f.triangles]
    centre = p.mean(axis=1)
    d1, d2 = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
    area = 0.5 * np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])
    magnitude = np.hypot(*f.current_density.T)
    peak = int(np.argmax(magnitude))
    tree = cKDTree(centre)
    best, where = 0.0, centre[peak]
    for i in np.argsort(magnitude)[::-1][:300]:
        near = tree.query_ball_point(centre[i], AVERAGE_RADIUS_M)
        value = float(np.sum(magnitude[near] * area[near]) / np.sum(area[near]))
        if value > best:
            best, where = value, centre[i]
    return HotSpot(f.net, f.layer_id, float(magnitude[peak]), tuple(map(float, centre[peak])),
                   best, tuple(map(float, where)))


def _solve_net(board, clean, net, role, source, loads, setup, scale):
    """Mesh and solve one net; GND voltages returned as the rise above the source."""
    started = perf_counter()
    mesh = mesh_net(clean, board, net, MeshSizes(scale=scale))
    field_of = "power_pins" if role == "power" else "gnd_pins"
    groups = {g.name: getattr(g, field_of) for g in (source, *loads)}
    network = build_net_network(board, clean, mesh, groups=groups,
                                resistivity=setup.resistivity_ohm_m,
                                barrel_resistivity=setup.barrel_resistivity_ohm_m)
    src = f"group:{source.name}"
    prepared = network.network.prepare([src])
    fixed = source.volts if role == "power" else 0.0
    result = prepared.solve({src: fixed}, {f"group:{g.name}": g.amps for g in loads})
    if not result.passed:
        raise RuntimeError(f"net {net!r}: the solver's conservation checks failed: {result.checks}")
    sign = 1.0 if role == "power" else -1.0        # GND: loads drew; negate to get the rise
    layers = {layer.id: layer for layer in board.layers}
    fields, pins = [], []
    branch = result.branch_currents_a
    rim_owner = network.rim_terminals
    for L in mesh.layers:
        voltage = sign * np.asarray(result.sheet_voltages_v[L.layer_id], dtype=float)
        g_sheet = network.sheet_conductance_s[L.layer_id]
        thickness = layers[L.layer_id].thickness_m
        density = -g_sheet * _gradient(L.nodes, L.triangles, voltage) / thickness * 1e-6
        fields.append(LayerField(net, L.layer_id, layers[L.layer_id].z_m, L.nodes, L.triangles,
                                 voltage, density, float(result.sheet_losses_w[L.layer_id])))
        # Pin currents: what each pin's pad feeds into the sheet, less what its barrels carry.
        residual = sign * (assemble_sheet(L.nodes, L.triangles, g_sheet) @ (sign * voltage))
        for terminal, labels in network.groups.items():
            for label in labels:
                layer_id, nodes = network.pin_nodes[label]
                if layer_id != L.layer_id:
                    continue
                current = float(residual[nodes].sum())
                for (drill_id, rim_layer), owner in rim_owner.items():
                    if owner != terminal or rim_layer != layer_id:
                        continue
                    if not np.isin(L.hole_rims[drill_id], nodes).all():
                        continue
                    for b in network.barrels:
                        if b.drill_id != drill_id:
                            continue
                        if b.upper_layer_id == layer_id:
                            current += sign * branch[b.name]     # current leaving down the barrel
                        elif b.lower_layer_id == layer_id:
                            current -= sign * branch[b.name]
                group = terminal.split(":", 1)[1].split("@", 1)[0]
                pins.append(PinResult(net, group, label, layer_id, current))
    drills = {d.id: d for d in board.drills}
    by_drill = {}
    for b in network.barrels:
        by_drill[b.drill_id] = max(by_drill.get(b.drill_id, 0.0), abs(branch[b.name]))
    vias = tuple(sorted((ViaResult(net, d, drills[d].kind, drills[d].position_m, i)
                         for d, i in by_drill.items()), key=lambda v: -v.current_a))
    group_v = {name: sign * float(result.terminal_voltages_v[f"group:{name}"][0]) for name in groups}
    return NetSolve(net, role, tuple(fields), tuple(pins), vias,
                    tuple(_hot_spot(f) for f in fields),
                    float(sum(result.branch_losses_w.values())), dict(result.checks),
                    sum(len(L.triangles) for L in mesh.layers), perf_counter() - started, group_v)


MEMORY_SAFETY = 1.5     # memory grows a little faster than the mesh (direct solver fill-in):
                        # measured 2026-11-22, the 3.8x larger round needed 20 % more than
                        # predicted without a margin


def _predicted_bytes(baseline, peak, growth):
    """Memory the next round needs: the part above the baseline grows with the mesh, with
    a safety margin."""
    return baseline + max(peak - baseline, 0) * growth * MEMORY_SAFETY


def simulate(bound, board, clean, *, max_refinements=3, log=None, memory_limit_bytes=None,
             notes=()):
    """RunResult for every rail of ``bound`` (a BoundSetup) on ``board``.

    With ``memory_limit_bytes``, a refinement round is not started when its predicted
    memory exceeds the limit or what is free now: the last completed round is reported,
    marked not settled, with the reason."""
    setup = bound.setup
    started = perf_counter()
    baseline = peak_bytes()
    rails = []
    for rail in bound.rails:
        history, previous, completed, counts, memory = [], None, None, [], []
        change, allowed = {}, {}
        for scale in SCALES[:max_refinements + 1]:
            need = None
            if completed is not None and baseline is not None:
                growth = counts[-1] / counts[-2] if len(counts) >= 2 else 4.0
                need = _predicted_bytes(baseline, peak_bytes(), growth)
            if need is not None and memory_limit_bytes:
                room = memory_limit_bytes
                free, now = available_bytes(), current_bytes()
                if free is not None and now is not None:
                    room = min(room, free + now)
                if need > room:
                    reason = (f"the refinement at scale {scale:g} would need about "
                              f"{need / 1024**3:.1f} GB; {room / 1024**3:.1f} GB allowed "
                              f"(limit {memory_limit_bytes / 1024**3:.1f} GB or free memory)")
                    if log:
                        log(f"rail {rail.power_net!r}: stopped before scale {scale:g}: {reason}")
                    rails.append(replace(completed, settled=False, unsettled_reason=reason))
                    break
            power = _solve_net(board, clean, rail.power_net, "power", rail.source, rail.loads,
                               setup, scale)
            gnd = _solve_net(board, clean, rail.gnd_net, "gnd", rail.source, rail.loads, setup,
                             scale)
            counts.append(power.triangles + gnd.triangles)
            memory.append((scale, need, peak_bytes()))
            loads = []
            for load in rail.loads:
                v_power = power.group_voltage_v[load.name]
                v_gnd = gnd.group_voltage_v[load.name]
                seen = v_power - v_gnd
                loads.append(LoadResult(load.name, rail.power_net, load.amps, v_power, v_gnd,
                                        seen, rail.source.volts - seen))
            history.append((scale, {r.name: r.v_seen for r in loads}))
            if log:
                peak = peak_bytes()
                log(f"rail {rail.power_net!r} scale {scale:g}: "
                    + ", ".join(f"{r.name} {r.v_seen:.6f} V" for r in loads)
                    + f" ({counts[-1]} triangles, {power.seconds + gnd.seconds:.1f} s"
                    + (f", peak memory {peak / 1024**3:.1f} GB" if peak else "")
                    + (f", predicted {need / 1024**3:.1f} GB)" if need else ")"))
            if previous is not None:
                change = {r.name: abs(r.v_seen - previous[r.name]) for r in loads}
                allowed = {r.name: setup.tolerance_v + setup.tolerance_rel * abs(r.drop)
                           for r in loads}
            completed = RailResult(rail.power_net, rail.gnd_net, rail.source.name,
                                   rail.source.volts, scale, tuple(loads), power, gnd,
                                   tuple(history), change, allowed, memory=tuple(memory))
            if previous is not None and all(change[n] <= allowed[n] for n in change):
                rails.append(completed)
                break
            previous = history[-1][1]
        else:
            worst = max(change, key=lambda n: change[n] - allowed[n])     # tolerance may be 0
            raise RefinementError(
                f"rail {rail.power_net!r}: the voltage at load {worst!r} still changed "
                f"{change[worst] * 1e6:.2f} µV (allowed {allowed[worst] * 1e6:.2f} µV) after "
                f"{max_refinements} refinements; history: "
                + "; ".join(f"scale {s:g}: {v[worst]:.6f} V" for s, v in history))
    return RunResult(tuple(rails), setup.plating_m, setup.resistivity_ohm_m,
                     setup.barrel_resistivity_ohm_m, setup.tolerance_v, setup.tolerance_rel,
                     perf_counter() - started, tuple(notes), memory_limit_bytes, peak_bytes())

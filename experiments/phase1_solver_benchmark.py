"""Reproducible synthetic preparation/load-case probe; no board input.

Run: uv run python experiments/phase1_solver_benchmark.py
Samples at untied mesh vertices exercise RHS scaling only, not physical contact
accuracy. Timings are observations, not performance acceptance thresholds.
Each mesh is released before the next case is built.
The primary probe uses zero source reference (negative voltages are drops).
A separate 3.3 V probe records common-mode sensitivity of the existing checks;
it is reported even if a conservation gate fails.
"""

from dataclasses import asdict
import json
import platform
from time import perf_counter

import numpy as np
import scipy

from pdnlab_dc.checks import balance_checks, sheet_losses
from pdnlab_dc.fem import assemble_sheet
from pdnlab_dc.simple_meshes import rectangle_mesh
from pdnlab_dc.solver import prepare_fixed_voltages


def measure(nx, ny, ports=24, cases=8, batch_size=8):
    started = perf_counter()
    nodes, triangles, left, _ = rectangle_mesh(.1, .02, nx, ny)
    matrix = assemble_sheet(nodes, triangles, 35e-6/1.724e-8)
    assembly_s = perf_counter() - started
    prepared = prepare_fixed_voltages(matrix, left)
    candidates = np.setdiff1d(np.arange(len(nodes)), left)
    load_nodes = candidates[np.linspace(0, len(candidates)-1, ports, dtype=int)]
    current = np.zeros(len(nodes))
    current[load_nodes] = -.01
    direct_started = perf_counter()
    for scale in range(1, cases+1):
        voltage = prepared.solve(0., scale*current)
    direct_s = perf_counter() - direct_started
    direct_stats = asdict(prepared.last_solve)
    checks = balance_checks(matrix, voltage, left,
                            sheet_losses(nodes, triangles, 35e-6/1.724e-8, voltage).sum(),
                            injected_current=cases*current)
    transfer = prepared.transfer_resistance(load_nodes, batch_size=batch_size)
    transfer_stats = asdict(prepared.last_solve)
    difference = float(np.max(np.abs(
        -voltage[load_nodes] - transfer @ (-cases*current[load_nodes]))))
    if not checks["passed"] or difference > 1e-9:
        raise RuntimeError("synthetic benchmark failed its numerical checks")
    common_mode_v = prepared.solve(3.3, cases*current)
    common_mode_checks = balance_checks(
        matrix, common_mode_v, left,
        sheet_losses(nodes, triangles, 35e-6/1.724e-8, common_mode_v).sum(),
        injected_current=cases*current)
    return dict(nodes=len(nodes), triangles=len(triangles), ports=ports, cases=cases,
                assembly_s=assembly_s, preparation=asdict(prepared.stats),
                direct_cases_s=direct_s, last_direct=direct_stats,
                transfer=transfer_stats, max_direct_transfer_difference_v=difference,
                checks=checks, common_mode_probe=dict(
                    fixed_voltage_v=3.3, checks=common_mode_checks,
                    max_shift_difference_v=float(np.max(np.abs(common_mode_v-3.3-voltage)))))


def main():
    print(json.dumps(dict(python=platform.python_version(), numpy=np.__version__,
                          scipy=scipy.__version__,
                          memory_scope="sparse array storage only; not peak RSS or native workspace",
                          cases=[measure(100, 20), measure(160, 40)]), indent=2))


if __name__ == "__main__":
    main()

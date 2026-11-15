"""Named-network example: three synthetic strips and a branching via chain.

Run: uv run python experiments/phase1_network_cases.py
No board input or Gmsh required. Geometry is SI. Analytic strip/barrel sums
are references; no physical accuracy for a real 3D via is implied. Each mesh
level's model/factors are released before the next level is built.
"""

from dataclasses import asdict
import json

import numpy as np

from pdnlab_dc.network import Network
from pdnlab_dc.simple_meshes import rectangle_mesh


def run_case(nx, ny):
    rho, length, width = 1.724e-8, .05, .005
    thicknesses = [18e-6, 35e-6, 70e-6]
    spacings, ri, ro = [.8e-3, .05e-3], .15e-3, .175e-3
    net = Network()
    for i, thickness in enumerate(thicknesses):
        nodes, triangles, left, right = rectangle_mesh(length, width, nx, ny, jitter=.2)
        net.add_sheet(f"layer{i}", nodes, triangles, thickness/rho)
        net.add_terminal(f"rim{i}", f"layer{i}", left, rim=True)
        net.add_terminal(f"pad{i}", f"layer{i}", right)
    for i, spacing in enumerate(spacings):
        net.add_barrel(f"barrel{i}", f"rim{i}", f"rim{i+1}", length=spacing,
                       inner_radius=ri, outer_radius=ro, resistivity=rho)
    prepared = net.prepare(["pad0"])
    # Independent closed forms, not values recovered from the assembled matrix.
    sheets = rho*length/(width*np.array(thicknesses))
    barrels = rho*np.array(spacings)/(np.pi*(ro**2-ri**2))
    references = np.array([.3*(sheets[0]+barrels[0])+.1*sheets[1],
                           .3*(sheets[0]+barrels[0])+.2*(barrels[1]+sheets[2])])
    cases = []
    for scale in (1., 2.):
        result = prepared.solve({"pad0": 3.3}, {"pad1": .1*scale, "pad2": .2*scale})
        drops = np.array([3.3-result.terminal_voltages_v[f"pad{i}"][0] for i in (1, 2)])
        error = float(np.max(np.abs(drops-scale*references)/(scale*references)))
        branch_error = float(np.max(np.abs(np.array(list(result.branch_currents_a.values()))
                                           -scale*np.array([.3, .2]))/(scale*np.array([.3, .2]))))
        passed = bool(result.passed and error <= 1e-6 and branch_error <= 1e-6)
        cases.append(dict(scale=scale, terminal_drop_v=drops.tolist(),
                          analytic_drop_v=(scale*references).tolist(),
                          max_drop_relative_error=error, max_branch_relative_error=branch_error,
                          terminal_currents_a=result.terminal_currents_a,
                          branch_currents_a=result.branch_currents_a,
                          sheet_losses_w=result.sheet_losses_w, branch_losses_w=result.branch_losses_w,
                          timings_s=result.timings_s, checks=result.checks, passed=passed))
    return dict(nx=nx, ny=ny, preparation=asdict(prepared.stats), cases=cases,
                passed=all(case["passed"] for case in cases))


def main():
    levels = [run_case(8, 3), run_case(16, 6)]
    passed = all(level["passed"] for level in levels)
    print(json.dumps(dict(levels=levels, passed=passed), indent=2))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()

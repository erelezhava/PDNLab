"""Finite-area contact references and an explicitly nonphysical point diagnostic.

Run: uv run python experiments/phase1_contact_cases.py
Each function returns scalar summaries/checks, releasing its mesh and factors
before the next level. No board files, remeshing or external tools are needed.
"""

import json

import numpy as np
from shapely.geometry import Polygon, box

from pdnlab_dc.network import Network
from pdnlab_dc.simple_meshes import concentric_disk_mesh, rectangle_mesh

RHO, THICKNESS = 1.724e-8, 35e-6
G = THICKNESS/RHO


def disk_case(radial_cells, finite):
    nodes, triangles, outer = concentric_disk_mesh(.005, radial_cells)
    net = Network()
    net.add_sheet("disk", nodes, triangles, G)
    net.add_terminal("reference", "disk", outer)
    if finite:
        angles = np.arange(128)*2*np.pi/128
        polygon = Polygon(.00125*np.column_stack([np.cos(angles), np.sin(angles)]))
        net.add_contact("load", "disk", polygon)
    else:
        net.add_terminal("load", "disk", [0])
    prepared = net.prepare(["reference"])
    result = prepared.solve({"reference": 0.}, {"load": 1.})
    measured = -float(result.terminal_voltages_v["load"][0])
    reference = RHO*np.log(4)/(2*np.pi*THICKNESS)
    error = abs(measured/reference-1) if finite else None
    return dict(radial_cells=radial_cells, nodes=len(nodes),
                model="equipotential_area" if finite else "mathematical_point_diagnostic",
                resistance_ohm=measured, circular_reference_ohm=reference if finite else None,
                relative_error=error, contacts=prepared.contact_info, checks=result.checks,
                passed=bool(result.passed and (not finite or error <= .005)))


def uniform_case(nx, ny):
    nodes, triangles, _, _ = rectangle_mesh(.05, .005, nx, ny)
    net = Network()
    net.add_sheet("strip", nodes, triangles, G)
    net.add_contact("reference", "strip", box(0, 0, .005, .005))
    net.add_contact("load", "strip", box(.0203, 0, .0307, .005), model="uniform_current")
    result = net.prepare(["reference"]).solve({"reference": 3.3}, {"load": 1.})
    measured = 3.3-result.contact_voltages_v["load"]
    # Area-mean drop/I and total sheet heat/I² for a uniform full-width sink.
    reference = (.0203-.005+(.0307-.0203)/3)/(G*.005)
    error = abs(measured/reference-1)
    loss_error = abs(result.sheet_losses_w["strip"]/reference-1)
    return dict(nx=nx, ny=ny, nodes=len(nodes), mean_contact_resistance_ohm=measured,
                analytic_mean_resistance_ohm=reference, relative_error=error,
                sheet_loss_relative_error=loss_error, checks=result.checks,
                passed=bool(result.passed and max(error, loss_error) <= .005))


def main():
    finite = [disk_case(n, True) for n in (16, 32, 64)]
    point = [disk_case(n, False) for n in (16, 32, 64)]
    uniform = [uniform_case(nx, ny) for nx, ny in ((40, 8), (80, 16), (160, 32))]
    convergence = all(b["relative_error"] < a["relative_error"]
                      for group in (finite, uniform) for a, b in zip(group, group[1:]))
    point_growth = all(b["resistance_ohm"] > a["resistance_ohm"] for a, b in zip(point, point[1:]))
    passed = bool(all(row["passed"] for row in finite+point+uniform) and convergence and point_growth)
    print(json.dumps(dict(finite_electrode=finite, point_diagnostic=point, uniform_patch=uniform,
                          decreasing_finite_errors=convergence, growing_point_voltage=point_growth,
                          passed=passed), indent=2))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()

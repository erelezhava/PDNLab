"""Experiment 4: two sheets, separate tied rims, one barrel resistor.

Run: uv run python experiments/exp04_two_rings.py
Append --output-dir /tmp/pdnlab-exp04 to retain geometry, meshes and JSON.
The top outer rim is at 1 V, the bottom outer rim at 0 V.
"""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import scipy.sparse as sp

from pdnlab_dc.fem import add_resistors, assemble_sheet, resistance_between, tie_nodes
from pdnlab_dc.checks import balance_checks, resistor_losses, sheet_losses
from pdnlab_dc.gmsh_mesh import read_sheet_mesh, run_gmsh, write_annulus_geo
from pdnlab_dc.vias import barrel_resistance

RHO_COPPER = 1.724e-8
LIMIT = 0.005
# name, finished hole diameter, plating, layer-centre spacing,
# top/bottom outer radii, top/bottom copper thickness, target mesh size [m]
CASES = [
    ("via_scale", 0.3e-3, 25e-6, 1.6e-3, (1.5e-3, 1.5e-3), (35e-6, 35e-6), 0.05e-3),
    ("unequal", 0.3e-3, 25e-6, 0.05e-3, (1e-3, 2e-3), (18e-6, 70e-6), 0.05e-3),
    ("sheet_dominated", 2e-3, 100e-6, 0.005e-3, (5e-3, 3e-3), (18e-6, 35e-6), 0.25e-3),
]


def experiment(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for name, hole, plating, length, radii, thicknesses, h in CASES:
        ri, ro = hole / 2, hole / 2 + plating
        barrel_r = barrel_resistance(length, ri, ro, RHO_COPPER)
        meshes, matrices, layers = [], [], []
        for layer, b, t in zip(["top", "bottom"], radii, thicknesses):
            geo = output_dir / f"{name}_{layer}.geo"
            msh = geo.with_suffix(".msh")
            write_annulus_geo(geo, ro, b, h)
            version = run_gmsh(geo, msh)
            mesh = read_sheet_mesh(msh, boundaries=("inner", "outer"))
            meshes.append(mesh)
            matrices.append(assemble_sheet(mesh.nodes, mesh.triangles, t / RHO_COPPER))
            layers.append(dict(
                layer=layer, gmsh_version=version, outer_radius_m=b, thickness_m=t,
                nodes=len(mesh.nodes), triangles=len(mesh.triangles),
                reference_ring_ohm=float(RHO_COPPER * np.log(b / ro) / (2 * np.pi * t)),
            ))
        offset = len(meshes[0].nodes)
        sheet_matrix = sp.block_diag(matrices, format="csr")
        rims = [meshes[0].boundaries["inner"], meshes[1].boundaries["inner"] + offset]
        reduced_sheets, mapping = tie_nodes(sheet_matrix, rims)
        rim_nodes = [int(mapping[group[0]]) for group in rims]
        network = add_resistors(reduced_sheets, [rim_nodes], barrel_r)
        high = mapping[meshes[0].boundaries["outer"]]
        low = mapping[meshes[1].boundaries["outer"] + offset]
        resistance, v = resistance_between(network, high, low)
        barrel_current = float((v[rim_nodes[0]] - v[rim_nodes[1]]) / barrel_r)
        expanded_v = v[mapping]
        for i, (mesh, t) in enumerate(zip(meshes, thicknesses)):
            layer_v = expanded_v[:offset] if i == 0 else expanded_v[offset:]
            layers[i]["sheet_loss_w"] = float(sheet_losses(
                mesh.nodes, mesh.triangles, t / RHO_COPPER, layer_v).sum())
        sheet_current = reduced_sheets @ v
        # All contributions use current leaving the rim. Sheet and barrel
        # currents therefore cancel separately at each contacted layer.
        rim_currents = {
            "top": [float(sheet_current[rim_nodes[0]]), barrel_current],
            "bottom": [float(sheet_current[rim_nodes[1]]), -barrel_current],
        }
        barrel_loss = float(resistor_losses(v, [rim_nodes], barrel_r).sum())
        checks = balance_checks(network, v, np.r_[high, low],
                                sum(x["sheet_loss_w"] for x in layers), barrel_loss,
                                rim_currents=rim_currents)
        reference = sum(row["reference_ring_ohm"] for row in layers) + barrel_r
        error = abs(resistance - reference) / reference
        voltage_drop = float(v[rim_nodes[0]] - v[rim_nodes[1]])
        # Check the barrel's share separately, so a missing or shorted branch
        # cannot hide inside a sheet-dominated total resistance.
        reference_drop = barrel_r / reference  # 1 V terminal excitation
        drop_error = abs(voltage_drop - reference_drop) / reference_drop
        passed = bool(error <= LIMIT and drop_error <= LIMIT
                      and rim_nodes[0] != rim_nodes[1] and checks["passed"])
        results.append(dict(
            case=name, finished_hole_diameter_m=hole, plating_m=plating,
            barrel_inner_radius_m=ri, sheet_hole_radius_m=ro,
            layer_centre_spacing_m=length, mesh_size_m=h, rho_ohm_m=RHO_COPPER,
            layers=layers, rim_nodes=rim_nodes, electrical_nodes=network.shape[0],
            barrel_resistance_ohm=barrel_r, reference_total_ohm=reference,
            resistance_ohm=float(resistance), relative_error=float(error),
            barrel_voltage_drop_v=voltage_drop, reference_barrel_drop_v=reference_drop,
            barrel_drop_relative_error=float(drop_error), barrel_current_a=barrel_current,
            relative_error_limit=LIMIT, checks=checks, passed=passed,
        ))
        print(f"{name:16s} R={resistance:.9e} ohm  error={error:.4%}  "
              f"barrel drop={voltage_drop:.6e} V  {'PASS' if passed else 'FAIL'}")
    (output_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return all(row["passed"] for row in results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.output_dir is None:
        with TemporaryDirectory(prefix="pdnlab-exp04-") as directory:
            passed = experiment(Path(directory))
    else:
        passed = experiment(args.output_dir)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()

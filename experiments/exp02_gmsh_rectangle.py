"""Experiment 2: .geo -> external Gmsh -> tagged .msh -> FEM resistance.

Run: uv run python experiments/exp02_gmsh_rectangle.py
Keep geometry, meshes and JSON: append --output-dir /tmp/pdnlab-exp02
"""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from pdnlab_dc.fem import assemble_sheet, resistance_between
from pdnlab_dc.checks import balance_checks, sheet_losses
from pdnlab_dc.gmsh_mesh import read_sheet_mesh, run_gmsh, write_rectangle_geo

RHO_COPPER = 1.724e-8
LIMIT = 1e-6
CASES = [
    ("coarse", 50e-3, 5e-3, 35e-6, 5e-3),
    ("fine", 50e-3, 5e-3, 35e-6, 1e-3),
    ("thin_trace", 100e-3, 0.2e-3, 18e-6, 0.2e-3),
    ("wide", 2e-3, 80e-3, 70e-6, 1e-3),
]


def experiment(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for name, length, width, thickness, size in CASES:
        geo, msh = output_dir / f"{name}.geo", output_dir / f"{name}.msh"
        write_rectangle_geo(geo, length, width, size)
        version = run_gmsh(geo, msh)
        mesh = read_sheet_mesh(msh)
        matrix = assemble_sheet(mesh.nodes, mesh.triangles, thickness / RHO_COPPER)
        resistance, v = resistance_between(matrix, mesh.boundaries["left"], mesh.boundaries["right"])
        loss = float(sheet_losses(mesh.nodes, mesh.triangles, thickness / RHO_COPPER, v).sum())
        checks = balance_checks(matrix, v, np.r_[mesh.boundaries["left"], mesh.boundaries["right"]], loss)
        reference = RHO_COPPER * length / (width * thickness)
        error = abs(resistance - reference) / reference
        voltage_error = float(np.max(np.abs(v - (1 - mesh.nodes[:, 0] / length))))
        passed = bool(error <= LIMIT and voltage_error <= LIMIT and checks["passed"])
        results.append(dict(
            case=name, gmsh_version=version, nodes=len(mesh.nodes),
            triangles=len(mesh.triangles), length_m=length, width_m=width,
            thickness_m=thickness, mesh_size_m=size, rho_ohm_m=RHO_COPPER,
            resistance_ohm=float(resistance), reference_ohm=reference,
            relative_error=float(error), max_voltage_error_v=voltage_error,
            checks=checks, passed=passed,
        ))
        print(f"{name:12s} {len(mesh.nodes):6d} nodes  R={resistance:.9e} ohm  "
              f"relative error={error:.2e}  {'PASS' if passed else 'FAIL'}")
    (output_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"Gmsh {version}; physical groups: copper, left, right")
    return all(row["passed"] for row in results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.output_dir is not None:
        passed = experiment(args.output_dir)
    else:
        with TemporaryDirectory(prefix="pdnlab-exp02-") as directory:
            passed = experiment(Path(directory))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()

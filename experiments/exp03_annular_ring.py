"""Experiment 3: annular sheet, tied inner rim and mesh convergence.

Run: uv run python experiments/exp03_annular_ring.py
Append --output-dir /tmp/pdnlab-exp03 to keep geometry, meshes and JSON.
Reference: R = rho / (2*pi*t) * ln(b/a). All dimensions are in SI units.
"""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from pdnlab_dc.fem import assemble_sheet, resistance_between, tie_nodes
from pdnlab_dc.checks import balance_checks, sheet_losses
from pdnlab_dc.gmsh_mesh import read_sheet_mesh, run_gmsh, write_annulus_geo

RHO_COPPER = 1.724e-8
LIMIT = 0.005
# name, inner radius, outer radius, thickness, coarse element size [metres]
CASES = [
    ("ring", 1e-3, 5e-3, 35e-6, 0.25e-3),
    ("via_scale", 0.15e-3, 1.5e-3, 18e-6, 0.05e-3),
    ("narrow", 3e-3, 6e-3, 70e-6, 0.4e-3),
]


def experiment(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for name, a, b, thickness, size in CASES:
        levels = []
        reference = RHO_COPPER * np.log(b / a) / (2 * np.pi * thickness)
        for level, h in [("coarse", size), ("fine", size / 2)]:
            geo = output_dir / f"{name}_{level}.geo"
            msh = geo.with_suffix(".msh")
            write_annulus_geo(geo, a, b, h)
            version = run_gmsh(geo, msh)
            mesh = read_sheet_mesh(msh, boundaries=("inner", "outer"))
            matrix = assemble_sheet(mesh.nodes, mesh.triangles, thickness / RHO_COPPER)
            inner, outer = mesh.boundaries["inner"], mesh.boundaries["outer"]
            reduced, mapping = tie_nodes(matrix, [inner])
            high = np.unique(mapping[inner])
            resistance, reduced_v = resistance_between(reduced, high, mapping[outer])
            v = reduced_v[mapping]
            loss = float(sheet_losses(mesh.nodes, mesh.triangles, thickness / RHO_COPPER, v).sum())
            checks = balance_checks(reduced, reduced_v, np.r_[high, mapping[outer]], loss)
            expected_v = np.log(b / np.linalg.norm(mesh.nodes, axis=1)) / np.log(b / a)
            error = abs(resistance - reference) / reference
            levels.append(dict(
                level=level, gmsh_version=version, mesh_size_m=h,
                nodes=len(mesh.nodes), electrical_nodes=reduced.shape[0],
                triangles=len(mesh.triangles), inner_rim_nodes=len(inner),
                resistance_ohm=float(resistance), relative_error=float(error),
                max_voltage_error_v=float(np.max(np.abs(v - expected_v))),
                inner_rim_spread_v=float(np.ptp(v[inner])),
                checks=checks,
            ))
            print(f"{name:10s} {level:6s} {len(mesh.nodes):6d} nodes  "
                  f"R={resistance:.9e} ohm  error={error:.3%}")
        passed = bool(
            all(row["relative_error"] <= LIMIT and row["inner_rim_spread_v"] == 0
                and row["checks"]["passed"] for row in levels)
            and levels[1]["relative_error"] < levels[0]["relative_error"]
        )
        results.append(dict(
            case=name, inner_radius_m=a, outer_radius_m=b, thickness_m=thickness,
            rho_ohm_m=RHO_COPPER, reference_ohm=float(reference),
            relative_error_limit=LIMIT, levels=levels, passed=passed,
        ))
        print(f"  {'PASS' if passed else 'FAIL'}: limit 0.5%; error must decrease at h/2")
    (output_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return all(row["passed"] for row in results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.output_dir is None:
        with TemporaryDirectory(prefix="pdnlab-exp03-") as directory:
            passed = experiment(Path(directory))
    else:
        passed = experiment(args.output_dir)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()

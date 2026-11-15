"""Phase 0, experiment 1: plain rectangle on a hand-made mesh.

Run with:  uv run python experiments/exp01_rectangle.py

Prints the solved resistance of a copper strip next to rho * L / (W * t).
Pass criterion from the plan: relative error at or below 1e-6.
"""

import argparse
import json
from pathlib import Path

import numpy as np

from pdnlab_dc.checks import balance_checks, sheet_losses
from pdnlab_dc.fem import assemble_sheet, resistance_between
from pdnlab_dc.simple_meshes import rectangle_mesh

RHO_COPPER = 1.724e-8  # ohm metre, annealed copper at 20 C
LIMIT = 1e-6

# length [m], width [m], thickness [m], nx, ny, jitter
CASES = [
    (50e-3, 5e-3, 35e-6, 1, 1, 0.0),
    (50e-3, 5e-3, 35e-6, 10, 3, 0.0),
    (50e-3, 5e-3, 35e-6, 40, 8, 0.2),
    (100e-3, 0.2e-3, 18e-6, 200, 2, 0.2),
    (2e-3, 80e-3, 70e-6, 4, 60, 0.2),
    (200e-3, 200e-3, 35e-6, 120, 120, 0.2),
]


def experiment(output_dir=None):
    print(f"{'L mm':>7} {'W mm':>7} {'t um':>5} {'nodes':>6} "
          f"{'R solved, ohm':>16} {'R formula, ohm':>16} {'rel. error':>11}  result")
    all_pass = True
    results = []
    for length, width, thickness, nx, ny, jitter in CASES:
        nodes, triangles, left, right = rectangle_mesh(
            length, width, nx, ny, "alternate", jitter
        )
        matrix = assemble_sheet(nodes, triangles, thickness / RHO_COPPER)
        resistance, v = resistance_between(matrix, left, right)
        loss = float(sheet_losses(nodes, triangles, thickness / RHO_COPPER, v).sum())
        checks = balance_checks(matrix, v, np.r_[left, right], loss)
        reference = RHO_COPPER * length / (width * thickness)
        error = abs(resistance - reference) / reference
        passed = bool(error <= LIMIT and checks["passed"])
        results.append(dict(length_m=length, width_m=width, thickness_m=thickness,
                            nodes=len(nodes), resistance_ohm=float(resistance),
                            reference_ohm=reference, relative_error=float(error),
                            checks=checks, passed=passed))
        all_pass = all_pass and passed
        print(f"{length * 1e3:7.1f} {width * 1e3:7.1f} {thickness * 1e6:5.0f} {len(nodes):6d} "
              f"{resistance:16.9e} {reference:16.9e} {error:11.1e}  {'PASS' if passed else 'FAIL'}")
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return all_pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    raise SystemExit(0 if experiment(args.output_dir) else 1)


if __name__ == "__main__":
    main()

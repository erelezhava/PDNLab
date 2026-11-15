"""Experiment 5: rerun experiments 1-4 with independent conservation checks.

Run: uv run python experiments/exp05_balances.py
Append --output-dir /tmp/pdnlab-exp05 to retain every result and a summary.
Any numerical or balance gate failure makes this command exit nonzero.
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


SCRIPTS = ["exp01_rectangle.py", "exp02_gmsh_rectangle.py",
           "exp03_annular_ring.py", "exp04_two_rings.py"]


def experiment(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for script in SCRIPTS:
        destination = output_dir / Path(script).stem
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name(script)),
             "--output-dir", str(destination)], capture_output=True, text=True,
            timeout=300,
        )
        if result.returncode != 0:
            print(result.stdout + result.stderr)
            raise RuntimeError(f"{script} failed with exit code {result.returncode}")
        rows = json.loads((destination / "results.json").read_text())
        checks = [level["checks"] for row in rows for level in row.get("levels", [row])]
        rims = [rim for check in checks for rim in check["via_rims"].values()]
        passed = all(row["passed"] for row in rows) and all(x["passed"] for x in checks)
        entry = dict(
            experiment=Path(script).stem, solves=len(checks), via_rim_checks=len(rims),
            max_current_residual_a=max(x["current_balance"]["residual_a"] for x in checks),
            max_power_residual_w=max(x["power_balance"]["residual_w"] for x in checks),
            max_rim_residual_a=max((x["residual_a"] for x in rims), default=None),
            max_current_tolerance_fraction=max(x["current_balance"]["residual_a"] /
                                              x["current_balance"]["tolerance_a"] for x in checks),
            max_power_tolerance_fraction=max(x["power_balance"]["residual_w"] /
                                            x["power_balance"]["tolerance_w"] for x in checks),
            max_rim_tolerance_fraction=max((x["residual_a"] / x["tolerance_a"] for x in rims), default=None),
            passed=passed,
        )
        summary.append(entry)
        print(f"{entry['experiment']:24s} {len(checks)} solves, {len(rims)} via rims: "
              f"{'PASS' if passed else 'FAIL'}")
    (output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return all(x["passed"] for x in summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.output_dir is None:
        with TemporaryDirectory(prefix="pdnlab-exp05-") as directory:
            passed = experiment(Path(directory))
    else:
        passed = experiment(args.output_dir.resolve())
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()

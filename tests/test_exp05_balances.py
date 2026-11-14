"""Run all four experiments through their public scripts and inspect gates."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(shutil.which("gmsh") is None, reason="external Gmsh not installed")
def test_all_experiments_conserve_current_and_power(tmp_path):
    script = Path(__file__).resolve().parents[1] / "experiments" / "exp05_balances.py"
    result = subprocess.run([sys.executable, str(script), "--output-dir", str(tmp_path)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((tmp_path / "results.json").read_text())
    assert len(summary) == 4
    assert sum(x["solves"] for x in summary) == 19
    assert sum(x["via_rim_checks"] for x in summary) == 6
    for entry in summary:
        assert entry["passed"]
        assert entry["max_current_tolerance_fraction"] <= 1
        assert entry["max_power_tolerance_fraction"] <= 1
        rows = json.loads((tmp_path / entry["experiment"] / "results.json").read_text())
        for row in rows:
            assert row["passed"]
            for level in row.get("levels", [row]):
                check = level["checks"]
                assert check["passed"]
                assert check["current_balance"]["passed"]
                assert check["power_balance"]["passed"]
                if entry["experiment"] == "exp04_two_rings":
                    assert set(check["via_rims"]) == {"top", "bottom"}
                    assert all(x["passed"] for x in check["via_rims"].values())
                    assert check["power_balance"]["lumped_loss_w"] > 0

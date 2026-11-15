"""Optional integration audit against the locally installed, unmodified demo."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


DEMO = Path("/usr/share/kicad/demos/stickhub/StickHub.kicad_pcb")


@pytest.mark.skipif(shutil.which("kicad-cli") is None or not DEMO.is_file(),
                    reason="KiCad CLI and StickHub demo required")
def test_export_and_compare_real_board(tmp_path):
    version = subprocess.run(["kicad-cli", "version"], capture_output=True,
                             text=True, check=True, timeout=30).stdout.strip()
    if not version.startswith("10."):
        pytest.skip("KiCad 10 required")
    before = hashlib.sha256(DEMO.read_bytes()).hexdigest()
    script = Path(__file__).resolve().parents[1] / "experiments" / "exp06_kicad_export.py"
    result = subprocess.run([sys.executable, str(script), "--board", str(DEMO),
                             "--output-dir", str(tmp_path)], capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "results.json").read_text())
    assert report["passed"]
    assert all(report["gates"].values())
    assert report["source_fill_polygons"] > 0
    assert report["matched_fill_polygons"] == report["source_fill_polygons"]
    assert report["matched_pin_net_mappings"] == report["source_pin_net_mappings"]
    assert report["matched_vias"] == report["source_vias"]
    assert before == report["board_sha256"] == hashlib.sha256(DEMO.read_bytes()).hexdigest()

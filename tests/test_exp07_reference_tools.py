"""A missing reference tool must never turn into a successful comparison."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


DEMO = Path("/usr/share/kicad/demos/stickhub/StickHub.kicad_pcb")


@pytest.mark.skipif(not DEMO.is_file(), reason="installed StickHub project required")
def test_prepare_missing_tools_records_blocker_not_pass(tmp_path):
    source_hash = hashlib.sha256(DEMO.read_bytes()).hexdigest()
    script = Path(__file__).resolve().parents[1] / "experiments" / "exp07_reference_tools.py"
    result = subprocess.run([sys.executable, str(script), "--output-dir", str(tmp_path),
                             "--padne", str(tmp_path / "absent-padne"),
                             "--kipi-root", str(tmp_path / "absent-KiPI")],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 2, result.stdout + result.stderr
    report = json.loads((tmp_path / "results.json").read_text())
    assert not report["passed"]
    assert report["status"] == "blocked_pending_reference_runs"
    for name in ["padne", "kipi"]:
        assert report[name]["status"] == "blocked"
        assert report[name]["reference_voltages"] is None
    manifest = json.loads((tmp_path / "benchmark.json").read_text())
    assert manifest["board_sha256"] == source_hash
    assert hashlib.sha256((tmp_path / "board" / DEMO.name).read_bytes()).hexdigest() == source_hash
    assert hashlib.sha256(DEMO.read_bytes()).hexdigest() == source_hash
    assert manifest["load"]["return_terminal"] == manifest["source"]["reference"]
    assert manifest["load"]["current_a"] == 0.5
    directives = (tmp_path / "padne-directives.txt").read_text()
    assert "!padne VOLTAGE v=5V p=J2.1 n=J2.4" in directives
    assert "!padne CURRENT i=0.5A f=J7.1 t=J2.4" in directives

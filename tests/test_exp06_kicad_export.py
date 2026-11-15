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


@pytest.mark.parametrize('coverage,geometry,strict,passed', [
    (True, True, True, True),
    (True, True, False, True),
    (True, False, True, False),
    (True, False, False, True),
    (False, True, True, False),
])
def test_audit_message_describes_combined_coverage(tmp_path, monkeypatch, capsys,
                                                 coverage, geometry, strict, passed):
    import importlib.util
    from types import SimpleNamespace
    script = Path(__file__).resolve().parents[1] / 'experiments' / 'exp06_kicad_export.py'
    spec = importlib.util.spec_from_file_location('exp06_message', script)
    exp06 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exp06)
    board = tmp_path / 'board.kicad_pcb'
    board.write_text('(kicad_pcb)')

    def run(command, **kwargs):
        if command[1] == 'version':
            return SimpleNamespace(stdout='10.0.8', stderr='', returncode=0)
        Path(command[command.index('--output') + 1]).write_text('<IPC-2581/>')
        return SimpleNamespace(stdout='', stderr='', returncode=0)

    monkeypatch.setattr(exp06.subprocess, 'run', run)
    monkeypatch.setattr(exp06, 'inspect_export', lambda *args: dict(
        passed=coverage, gates={'zone_fills': coverage}, matched_fill_polygons=1,
        matched_pin_net_mappings=1, matched_vias=1, route_decision='obsolete message'))
    monkeypatch.setattr(exp06, 'inspect_geometry', lambda *args: dict(
        passed=geometry, gates={'tracks': geometry}, unsupported={},
        tracks={'matched': 1, 'source': 1}, pads={'matched': 1, 'source': 1},
        pad_holes={'matched': 1, 'source': 1}))
    out = tmp_path / 'out'
    assert exp06.experiment(board, out, strict=strict) is passed
    saved = json.loads((out / 'results.json').read_text())
    output = capsys.readouterr().out
    assert saved['route_decision'] in output
    assert 'obsolete message' not in output
    assert 'until tracks and pad geometry are audited' not in output
    if coverage and geometry:
        assert 'including tracks, pad copper and pad holes' in output
        assert 'maintainer confirmation is pending' in output
    else:
        assert 'incomplete or failed' in output

"""Prepare and preflight the StickHub comparison; never invent reference voltages.

Run: uv run python experiments/exp07_reference_tools.py --output-dir /tmp/pdnlab-exp07
Append --padne reference-tools/padne-linux-x64 --run-padne to attempt its solve.
KiPI requires a separate standalone installation and a configured DC run.
Exit 2 means reference measurements are still pending, not a numerical failure.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from pdnlab_dc.ipc2581_inspect import _source_data

DEFAULT_BOARD = Path("/usr/share/kicad/demos/stickhub/StickHub.kicad_pcb")


def command_result(command, timeout=30):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return dict(command=command, returncode=result.returncode,
                    stdout=result.stdout, stderr=result.stderr)
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as exc:
        return dict(command=command, returncode=None, error=str(exc))


def prepare(output_dir, board=DEFAULT_BOARD, padne="padne", kipi_root=None,
            kipi_python=None, run_padne=False):
    board = board.resolve()
    source = _source_data(board)
    endpoints = {("J2", "1", "+5V"), ("J2", "4", "GND"), ("J7", "1", "+5V")}
    missing = endpoints - source["pins"]
    if missing:
        raise ValueError(f"comparison endpoints absent or on unexpected nets: {sorted(missing)}")
    if any(source["thicknesses"].get(layer) != 35000 for layer in ["F.Cu", "B.Cu"]):
        raise ValueError("this prepared benchmark requires 35 um F.Cu and B.Cu")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    project_dir = output_dir / "board"
    project_dir.mkdir(exist_ok=True)
    # Copy only the native project inputs, never modify the installed demo.
    # These files are external reference data and should not be committed.
    for suffix in [".kicad_pcb", ".kicad_pro", ".kicad_sch"]:
        original = board.with_suffix(suffix)
        if not original.is_file():
            raise FileNotFoundError(original)
        target = project_dir / original.name
        if target.resolve() == original.resolve():
            raise ValueError("comparison output must be separate from the source project")
        shutil.copy2(original, target)
    if (board.parent / "LICENSE.md").is_file():
        shutil.copy2(board.parent / "LICENSE.md", project_dir / "LICENSE.md")

    manifest = dict(
        board_sha256=hashlib.sha256(board.read_bytes()).hexdigest(),
        source_board=str(board), working_board=str(project_dir / board.name),
        benchmark="synthetic single-rail +5V distribution; not the board's operating load model",
        source=dict(positive="J2.1", reference="J2.4", voltage_v=5.0),
        load=dict(positive="J7.1", current_a=0.5, return_terminal="J2.4"),
        return_model="ideal common return at source reference; exclude remote GND copper drop",
        resistivity_ohm_m=1.724e-8, temperature_c=20, plating_m=25e-6,
        etch_undercut_m=0, copper_thickness_m={"F.Cu": 35e-6, "B.Cu": 35e-6},
        probes=["J2.1", "J3.1", "J4.1", "J5.1", "J6.1", "J7.1", "J8.1"],
        observables=["pad voltage relative to J2.4", "5 V minus pad voltage"],
        settings_status="targets defined; actual tool material and mesh settings must be recorded after runs",
        scope_limits=["No active component, capacitor or series-part model.",
                      "No thermal solve or temperature feedback.",
                      "Tools must use the same ideal return and source/load pins before comparing."],
    )
    # Verify every probe too, so a later sample change cannot silently redirect it.
    for endpoint in manifest["probes"]:
        ref, pin = endpoint.split(".")
        if (ref, pin, "+5V") not in source["pins"]:
            raise ValueError(f"probe {endpoint} not on +5V")
    (output_dir / "benchmark.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    directives = output_dir / "padne-directives.txt"
    directives.write_text(
        "# Synthetic single-rail benchmark. All returns at source reference.\n"
        "!padne VOLTAGE v=5V p=J2.1 n=J2.4\n"
        "!padne CURRENT i=0.5A f=J7.1 t=J2.4\n"
        f"!padne COPPER conductivity={1 / manifest['resistivity_ohm_m']:.12g} plating=25um undercut=0\n"
        "!padne PROBE p=J2.1,J3.1,J4.1,J5.1,J6.1,J7.1,J8.1\n",
        encoding="utf-8",
    )
    resolved = shutil.which(str(padne))
    padne_report = dict(status="blocked", reason="padne executable not installed", reference_voltages=None)
    if resolved:
        version = command_result([resolved, "--version"])
        help_result = command_result([resolved, "solve", "--help"])
        padne_report.update(version_probe=version, solve_help=help_result,
                            executable_sha256=hashlib.sha256(Path(resolved).read_bytes()).hexdigest())
        help_text = help_result.get("stdout", "") + help_result.get("stderr", "")
        if version.get("returncode") != 0 or help_result.get("returncode") != 0:
            padne_report["reason"] = "installed executable failed version/help preflight"
        elif "--directives" not in help_text or "--no-schematic-directives" not in help_text:
            padne_report["reason"] = "this build lacks the documented external-directive options"
        else:
            solve = [resolved, "solve", str(project_dir / board.with_suffix('.kicad_pro').name),
                     str(output_dir / "solution.padne"), "--no-schematic-directives",
                     "--directives", str(directives)]
            padne_report.update(status="ready_to_attempt", reason="numerical run not attempted", solve_command=solve)
            if run_padne:
                solution = output_dir / "solution.padne"
                solution.unlink(missing_ok=True)
                run = command_result(solve, timeout=180)
                padne_report["solve_run"] = run
                if run.get("returncode") == 0 and solution.is_file():
                    padne_report.update(status="solution_created", reason="pad-voltage extraction and settings review pending")
                else:
                    padne_report.update(status="blocked", reason="numerical run failed; inspect solve_run output")

    kipi_report = dict(status="blocked", reason="KiPI standalone checkout not installed", reference_voltages=None)
    if kipi_root is not None:
        kipi_root = kipi_root.resolve()
        launcher = kipi_root / "kipi_launcher.py"
        if launcher.is_file():
            kipi_report.update(reason="configured DC run and measurement export pending", checkout=str(kipi_root),
                               revision_probe=command_result(["git", "-C", str(kipi_root), "rev-parse", "HEAD"]))
            if kipi_python is not None:
                probe = command_result([str(kipi_python.resolve()), "-c",
                    "import sys; print(sys.version); import kipi; print(kipi.__version__); import qtpy, pyvista, shapely, scipy, triangle, pyamg; print('dependencies import OK')"])
                kipi_report["dependency_probe"] = probe
                if probe.get("returncode") != 0:
                    kipi_report["reason"] = "standalone dependency preflight failed"

    report = dict(status="blocked_pending_reference_runs", passed=False,
                  benchmark_sha256=hashlib.sha256((output_dir / "benchmark.json").read_bytes()).hexdigest(),
                  padne=padne_report, kipi=kipi_report,
                  note="No reference voltage is recorded until a real solve and extraction are reviewed.")
    (output_dir / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {project_dir / board.name}")
    print(f"padne: {padne_report['status']} — {padne_report['reason']}")
    print(f"KiPI: {kipi_report['status']} — {kipi_report['reason']}")
    print("Experiment 7 remains pending; no reference voltages have been fabricated.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--board", type=Path, default=DEFAULT_BOARD)
    parser.add_argument("--padne", default="padne")
    parser.add_argument("--kipi-root", type=Path)
    parser.add_argument("--kipi-python", type=Path)
    parser.add_argument("--run-padne", action="store_true")
    args = parser.parse_args()
    prepare(args.output_dir, args.board, args.padne, args.kipi_root, args.kipi_python, args.run_padne)
    raise SystemExit(2)


if __name__ == "__main__":
    main()

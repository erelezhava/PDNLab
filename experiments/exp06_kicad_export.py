"""Experiment 6: export a real board and compare IPC-2581 against saved data.

Run: uv run python experiments/exp06_kicad_export.py
Default sample: KiCad's installed StickHub demo (read-only).
Use --board your.kicad_pcb --output-dir /tmp/pdnlab-exp06 for another board.
The track, pad and pad-hole audit is reported too; --strict makes it gate the exit code.
This audits export coverage; it does not solve or modify the board.
"""

import argparse
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from pdnlab_dc.ipc2581_geometry import inspect_geometry
from pdnlab_dc.ipc2581_inspect import inspect_export

DEFAULT_BOARD = Path("/usr/share/kicad/demos/stickhub/StickHub.kicad_pcb")


def experiment(board, output_dir, executable="kicad-cli", strict=False):
    board = board.resolve()
    if not board.is_file():
        raise FileNotFoundError(f"Board not found: {board}; provide --board path/to/board.kicad_pcb")
    output_dir.mkdir(parents=True, exist_ok=True)
    version = subprocess.run([executable, "version"], check=True, capture_output=True,
                             text=True, timeout=30).stdout.strip()
    if not version.startswith("10."):
        raise ValueError(f"experiment 6 requires KiCad 10, found {version}")
    xml = (output_dir / "board.xml").resolve()
    if xml == board:
        raise ValueError("export output must not overwrite the source board")
    xml.unlink(missing_ok=True)
    command = [executable, "pcb", "export", "ipc2581", "--units", "mm",
               "--precision", "6", "--version", "C", "--output", str(xml), str(board)]
    exported = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if exported.returncode != 0:
        raise RuntimeError(f"KiCad export failed:\n{exported.stdout}\n{exported.stderr}")
    report = inspect_export(board, xml)
    geometry = inspect_geometry(board, xml)
    report.update(kicad_version=version, source_board=str(board), export_command=command, geometry=geometry)
    (output_dir / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for gate, passed in report["gates"].items():
        print(f"{gate:16s} {'PASS' if passed else 'FAIL'}")
    for gate, passed in geometry["gates"].items():
        print(f"{gate:16s} {'PASS' if passed else 'FAIL'}")
    print(f"tracks {geometry['tracks']['matched']}/{geometry['tracks']['source']}, "
          f"pad copper {geometry['pads']['matched']}/{geometry['pads']['source']}, "
          f"pad holes {geometry['pad_holes']['matched']}/{geometry['pad_holes']['source']}"
          + (f"; unsupported: {geometry['unsupported']}" if geometry["unsupported"] else ""))
    print(f"KiCad {version}: {report['matched_fill_polygons']} filled polygons, "
          f"{report['matched_pin_net_mappings']} pin/net mappings, {report['matched_vias']} vias matched")
    print(report["route_decision"])
    print(f"geometry audit {'PASS' if geometry['passed'] else 'FAIL'} "
          f"({'gates the exit code' if strict else 'informational; use --strict to gate the exit code'})")
    return report["passed"] and (geometry["passed"] or not strict)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, default=DEFAULT_BOARD)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--strict", action="store_true",
                        help="also require the track, pad and pad-hole audit to pass")
    args = parser.parse_args()
    if args.output_dir is None:
        with TemporaryDirectory(prefix="pdnlab-exp06-") as directory:
            passed = experiment(args.board, Path(directory), strict=args.strict)
    else:
        passed = experiment(args.board, args.output_dir, strict=args.strict)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()

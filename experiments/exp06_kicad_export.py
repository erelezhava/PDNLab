"""Experiment 6: export a real board and compare IPC-2581 against saved data.

Run: uv run python experiments/exp06_kicad_export.py
Default sample: KiCad's installed StickHub demo (read-only).
Use --board your.kicad_pcb --output-dir /tmp/pdnlab-exp06 for another board.
This audits export coverage; it does not solve or modify the board.
"""

import argparse
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from pdnlab_dc.ipc2581_inspect import inspect_export

DEFAULT_BOARD = Path("/usr/share/kicad/demos/stickhub/StickHub.kicad_pcb")


def experiment(board, output_dir, executable="kicad-cli"):
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
    report.update(kicad_version=version, source_board=str(board), export_command=command)
    (output_dir / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for gate, passed in report["gates"].items():
        print(f"{gate:16s} {'PASS' if passed else 'FAIL'}")
    print(f"KiCad {version}: {report['matched_fill_polygons']} filled polygons, "
          f"{report['matched_pin_net_mappings']} pin/net mappings, {report['matched_vias']} vias matched")
    print(report["route_decision"])
    return report["passed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, default=DEFAULT_BOARD)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.output_dir is None:
        with TemporaryDirectory(prefix="pdnlab-exp06-") as directory:
            passed = experiment(args.board, Path(directory))
    else:
        passed = experiment(args.board, args.output_dir)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()

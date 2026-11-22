"""Command line.

``pdnlab-dc init BOARD.kicad_pcb`` exports the board with kicad-cli into a ``pdnlab/``
folder next to it and writes a setup file to fill in there (an IPC-2581 ``.xml`` works
too); ``pdnlab-dc check
SETUP.toml`` reads it, checks it against the board and says what it will simulate.
``pdnlab-dc run SETUP.toml`` solves it and writes the results to ``results/<setup
name>/`` next to the setup file (in a KiCad project: ``pdnlab/results/setup/``), never
replacing earlier results without ``--force``.
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

from .ipc2581_board import read_ipc2581_board
from .kicad import KicadError, export_ipc2581
from .setup import SetupError, bind_setup, read_setup, refresh_export, setup_template


def _protect(limit_bytes=None):
    """Hard memory ceiling for this command (and kicad-cli and Gmsh under it); returns the
    limit applied, or None where the RAM is unknown."""
    from .memory import apply_ceiling, default_limit_bytes
    limit = limit_bytes or default_limit_bytes()
    if limit:
        apply_ceiling(limit)
    return limit


def _init(args):
    _protect()
    source = Path(args.board)
    if source.suffix == ".kicad_pcb":
        # A KiCad board: everything goes in a pdnlab/ folder next to it.
        folder = source.parent / "pdnlab"
        board_path = folder / f"{source.stem}.xml"
        out = Path(args.output) if args.output else folder / "setup.toml"
    else:
        board_path = source
        out = Path(args.output) if args.output else source.with_name("setup.toml")
    if out.exists() and not args.force:
        print(f"{out} already exists; use --force to replace it", file=sys.stderr)
        return 1
    if source.suffix == ".kicad_pcb":
        try:
            export_ipc2581(source, board_path)
        except KicadError as error:
            print(f"export failed: {error}", file=sys.stderr)
            return 1
        print(f"exported {board_path}")
    try:
        board = read_ipc2581_board(board_path)
    except MemoryError:
        print(f"board not usable: out of memory reading {board_path} (the machine itself "
              "was protected)", file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError) as error:
        print(f"board not usable: {error}", file=sys.stderr)
        return 1
    # Paths are written relative to the setup file's folder.
    base = out.resolve().parent
    board_name = Path(os.path.relpath(board_path.resolve(), base)).as_posix()
    kicad_name = (Path(os.path.relpath(source.resolve(), base)).as_posix()
                  if source.suffix == ".kicad_pcb" else None)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(setup_template(board, board_name, kicad_pcb_name=kicad_name,
                                  min_pins=args.min_pins), encoding="utf-8")
    print(f"wrote {out}: fill in plating_um, the source and the load(s)")
    return 0


def _check(args):
    try:
        setup = read_setup(args.setup)
        _protect(setup.max_memory_bytes)
        if refresh_export(setup):
            print(f"re-exported {setup.board_path} (the .kicad_pcb was newer)")
        bound = bind_setup(setup, read_ipc2581_board(setup.board_path))
    except MemoryError:
        print("setup not usable: out of memory reading the board (the machine itself was "
              "protected)", file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError, KicadError) as error:
        # SetupError is a ValueError; board reader errors say what to fix.
        print(f"setup not usable: {error}", file=sys.stderr)
        return 1
    print(f"board {setup.board_path}, plating {setup.plating_m * 1e6:g} um, GND net {bound.gnd_net!r}")
    for rail in bound.rails:
        s = rail.source
        amps = sum(load.amps for load in rail.loads)
        print(f"rail {rail.power_net!r}: source {s.name!r} {s.volts:g} V on {', '.join(s.power_pins)}"
              f" (GND {', '.join(s.gnd_pins)}); {len(rail.loads)} load(s), {amps:g} A in total")
        for load in rail.loads:
            print(f"    load {load.name!r} {load.amps:g} A on {', '.join(load.power_pins)}"
                  f" (GND {', '.join(load.gnd_pins)})")
    for name, field, wildcard, added in bound.expanded:
        print(f"{name!r} {field}: {wildcard} added {added} pin(s)")
    return 0


def _run(args):
    from .copper_cleanup import clean_copper
    from .results import write_results
    from .memory import apply_ceiling, default_limit_bytes
    from .run import simulate
    try:
        setup = read_setup(args.setup)
        folder = setup.path.parent / "results" / setup.path.stem
        if folder.exists() and not args.force:
            print(f"{folder} already exists; use --force to replace it", file=sys.stderr)
            return 1
        if refresh_export(setup):
            print(f"re-exported {setup.board_path} (the .kicad_pcb was newer)")
        board = read_ipc2581_board(setup.board_path)
        bound = bind_setup(setup, board)
        limit = setup.max_memory_bytes or default_limit_bytes()
        notes = []
        if limit:
            notes.append(apply_ceiling(limit))
            print(f"memory limit {limit / 1024**3:.1f} GB; {notes[-1]}")
        run = simulate(bound, board, clean_copper(board, setup.plating_m), log=print,
                       memory_limit_bytes=limit, notes=notes)
    except MemoryError:
        print("not solved: out of memory under the hard ceiling; lower max_memory_gb's "
              "rounds or free memory (the machine itself was protected)", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, FileNotFoundError) as error:
        # Setup, island, refinement and kicad-cli errors all say what to fix; no traceback.
        print(f"not solved: {error}", file=sys.stderr)
        return 1
    if folder.exists():
        shutil.rmtree(folder)
    write_results(run, folder, f"{setup.path.name} on {setup.board_path.name}")
    print((folder / "summary.txt").read_text(encoding="utf-8"))
    print(f"results in {folder}")
    if not all(rail.settled for rail in run.rails):
        print("NOT SETTLED: see the summary; the results are from the last round that fit",
              file=sys.stderr)
        return 2
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pdnlab-dc", description="PDNLab DC: PCB IR drop")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="write a setup file to fill in, from a board")
    init.add_argument("board", help="the KiCad board (.kicad_pcb: exported to a pdnlab/ "
                      "folder next to it) or an IPC-2581 export (.xml)")
    init.add_argument("-o", "--output", help="setup file to write (default: setup.toml "
                      "next to the board)")
    init.add_argument("--min-pins", type=int, default=3,
                      help="list nets with at least this many pins (default 3)")
    init.add_argument("--force", action="store_true", help="replace an existing file")
    init.set_defaults(handler=_init)
    check = commands.add_parser("check", help="check a setup file against its board")
    check.add_argument("setup", help="the setup file (.toml)")
    check.set_defaults(handler=_check)
    run = commands.add_parser("run", help="solve a setup file and write the results")
    run.add_argument("setup", help="the setup file (.toml)")
    run.add_argument("--force", action="store_true", help="replace earlier results")
    run.set_defaults(handler=_run)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())

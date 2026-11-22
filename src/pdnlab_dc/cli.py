"""Command line.

``pdnlab-dc init BOARD.xml`` writes a setup file to fill in; ``pdnlab-dc check
SETUP.toml`` reads it, checks it against the board and says what it will simulate.
Solving (``pdnlab-dc run SETUP.toml``) comes with increment 13.
"""

import argparse
import os
import sys
from pathlib import Path

from .ipc2581_board import read_ipc2581_board
from .setup import SetupError, bind_setup, read_setup, setup_template


def _init(args):
    board_path = Path(args.board)
    out = Path(args.output) if args.output else board_path.with_name("setup.toml")
    if out.exists() and not args.force:
        print(f"{out} already exists; use --force to replace it", file=sys.stderr)
        return 1
    board = read_ipc2581_board(board_path)
    # The board path is written relative to the setup file's folder.
    relative = os.path.relpath(board_path.resolve(), out.resolve().parent)
    out.write_text(setup_template(board, Path(relative).as_posix(), min_pins=args.min_pins),
                   encoding="utf-8")
    print(f"wrote {out}: fill in plating_um, the source and the load(s)")
    return 0


def _check(args):
    try:
        setup = read_setup(args.setup)
        bound = bind_setup(setup, read_ipc2581_board(setup.board_path))
    except (SetupError, FileNotFoundError) as error:
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
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pdnlab-dc", description="PDNLab DC: PCB IR drop")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="write a setup file to fill in, from a board")
    init.add_argument("board", help="the board's IPC-2581 export (.xml)")
    init.add_argument("-o", "--output", help="setup file to write (default: setup.toml "
                      "next to the board)")
    init.add_argument("--min-pins", type=int, default=3,
                      help="list nets with at least this many pins (default 3)")
    init.add_argument("--force", action="store_true", help="replace an existing file")
    init.set_defaults(handler=_init)
    check = commands.add_parser("check", help="check a setup file against its board")
    check.add_argument("setup", help="the setup file (.toml)")
    check.set_defaults(handler=_check)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())

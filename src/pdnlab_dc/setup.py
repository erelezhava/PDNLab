"""The setup file: what to simulate, in TOML, by pins.

``read_setup(path)`` reads and checks the file on its own (format, keys, numbers);
``bind_setup(setup, board)`` checks it against the board and finds the nets from the
pins; ``setup_template(board, board_name)`` writes a form to fill in. Decided by Erekle
(2026-11-22, docs/DECISIONS.md):

* Sources and loads by pins. Each has ``power_pins`` and ``gnd_pins``; a group of pins
  is equipotential (joined, like an ideal die or connector) and its current divides
  by itself.
* Power and GND are both simulated. Nets are found from the pins: a group's pins must
  all be on one net. Every GND pin of the file is on the same GND net.
* Several rails in one file: each ``[[source]]`` defines a rail (its power net), each
  ``[[load]]`` belongs to the rail its power pins are on. One source per power net.
* ``plating_um`` is required; ``resistivity_ohm_m``, ``barrel_resistivity_ohm_m``,
  ``tolerance_uv`` and ``tolerance_percent`` are optional (1.724e-8 Ω·m and release
  gate 5, 10 µV + 1 %).
* Strict: an unknown key is an error naming it. The board path is relative to the
  setup file's folder.
* Remote sense comes after release 0.1.
"""

import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .board import BoardModel
from .board_network import COPPER_RESISTIVITY_OHM_M

TOP_KEYS = {"board", "plating_um", "source", "load", "options"}
GROUP_KEYS = {"name", "power_pins", "gnd_pins"}
OPTION_KEYS = {"resistivity_ohm_m", "barrel_resistivity_ohm_m", "tolerance_uv",
               "tolerance_percent"}


@dataclass(frozen=True)
class Source:
    name: str
    volts: float
    power_pins: tuple[str, ...]
    gnd_pins: tuple[str, ...]


@dataclass(frozen=True)
class Load:
    name: str
    amps: float
    power_pins: tuple[str, ...]
    gnd_pins: tuple[str, ...]


@dataclass(frozen=True)
class Setup:
    """A checked setup file, not yet compared with the board."""
    path: Path
    board_path: Path
    plating_m: float
    sources: tuple[Source, ...]
    loads: tuple[Load, ...]
    resistivity_ohm_m: float
    barrel_resistivity_ohm_m: float
    tolerance_v: float
    tolerance_rel: float


@dataclass(frozen=True)
class Rail:
    """One power net with its source and loads, and the GND net they return on."""
    power_net: str
    gnd_net: str
    source: Source
    loads: tuple[Load, ...]


@dataclass(frozen=True)
class BoundSetup:
    setup: Setup
    gnd_net: str
    rails: tuple[Rail, ...]


class SetupError(ValueError):
    """A problem in the setup file, with where it is."""


def _number(value, where, *, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SetupError(f"{where}: must be a number, got {value!r}")
    if value < 0 or (value == 0 and not allow_zero):
        raise SetupError(f"{where}: must be {'zero or more' if allow_zero else 'more than zero'}"
                         f", got {value!r}")
    return float(value)


def _keys(table, allowed, where, required=()):
    if not isinstance(table, dict):
        raise SetupError(f"{where}: must be a table")
    unknown = sorted(set(table) - set(allowed))
    if unknown:
        raise SetupError(f"{where}: unknown key {unknown[0]!r} (allowed: "
                         f"{', '.join(sorted(allowed))})")
    for key in required:
        if key not in table:
            raise SetupError(f"{where}: {key!r} is required")


def _pins(value, where):
    if not isinstance(value, list) or not value or not all(isinstance(p, str) for p in value):
        raise SetupError(f"{where}: must be a non-empty list of pin names like \"U1.12\"")
    pins = tuple(p.strip() for p in value)
    bad = [p for p in pins if not re.fullmatch(r"[^.\s]+\.[^\s]+", p)]
    if bad:
        raise SetupError(f"{where}: {bad[0]!r} is not a pin name like \"U1.12\"")
    if len(set(pins)) != len(pins):
        raise SetupError(f"{where}: a pin is listed twice")
    return pins


def _group(table, kind, value_key, index):
    where = f"[[{kind}]] #{index + 1}"
    _keys(table, GROUP_KEYS | {value_key}, where, required=("power_pins", "gnd_pins", value_key))
    name = table.get("name", f"{kind} {index + 1}")
    if not isinstance(name, str) or not name.strip():
        raise SetupError(f"{where}: 'name' must be a non-empty text")
    where = f"[[{kind}]] {name!r}"
    value = _number(table[value_key], f"{where} {value_key}", allow_zero=(kind == "load"))
    return name.strip(), value, _pins(table["power_pins"], f"{where} power_pins"), \
        _pins(table["gnd_pins"], f"{where} gnd_pins")


def parse_setup(text, path):
    """Setup from TOML text; ``path`` is where the file is (for the board path)."""
    path = Path(path)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise SetupError(f"{path.name}: not valid TOML: {error}") from error
    _keys(data, TOP_KEYS, path.name, required=("board", "plating_um", "source", "load"))
    if not isinstance(data["board"], str) or not data["board"].strip():
        raise SetupError("'board' must be the path of the IPC-2581 export")
    plating = _number(data["plating_um"], "plating_um") * 1e-6
    for kind in ("source", "load"):
        if not isinstance(data[kind], list) or not data[kind]:
            raise SetupError(f"at least one [[{kind}]] is required")
    sources = tuple(Source(*_group(t, "source", "volts", i)) for i, t in enumerate(data["source"]))
    loads = tuple(Load(*_group(t, "load", "amps", i)) for i, t in enumerate(data["load"]))
    names = [g.name for g in sources + loads]
    if len(set(names)) != len(names):
        raise SetupError(f"two sources or loads are both named "
                         f"{next(n for n in names if names.count(n) > 1)!r}")
    seen = {}
    for g in sources + loads:
        both = sorted(set(g.power_pins) & set(g.gnd_pins))
        if both:
            raise SetupError(f"pin {both[0]!r} is in both power_pins and gnd_pins of {g.name!r}")
        for pin in g.power_pins + g.gnd_pins:
            if pin in seen:
                raise SetupError(f"pin {pin!r} is used by both {seen[pin]!r} and {g.name!r}")
            seen[pin] = g.name
    options = data.get("options", {})
    _keys(options, OPTION_KEYS, "[options]")
    rho = _number(options.get("resistivity_ohm_m", COPPER_RESISTIVITY_OHM_M),
                  "[options] resistivity_ohm_m")
    rho_barrel = _number(options.get("barrel_resistivity_ohm_m", rho),
                         "[options] barrel_resistivity_ohm_m")
    tol_v = _number(options.get("tolerance_uv", 10), "[options] tolerance_uv") * 1e-6
    tol_rel = _number(options.get("tolerance_percent", 1), "[options] tolerance_percent") / 100
    board = Path(data["board"])
    board = board if board.is_absolute() else (path.parent / board)
    return Setup(path, board, plating, sources, loads, rho, rho_barrel, tol_v, tol_rel)


def read_setup(path):
    path = Path(path)
    return parse_setup(path.read_text(encoding="utf-8"), path)


def bind_setup(setup, board):
    """Check the setup against the board: pins exist, each group on one net, rails."""
    if not isinstance(board, BoardModel):
        raise ValueError("board must be a BoardModel")
    nets = {}
    for pad in board.pads:
        nets.setdefault(f"{pad.component_ref}.{pad.pin_number}", set()).add(pad.net_id)

    def net_of(pins, where):
        missing = [p for p in pins if p not in nets]
        if missing:
            raise SetupError(f"{where}: pin {missing[0]!r} is not on the board")
        found = {n for p in pins for n in nets[p]}
        if None in found:
            raise SetupError(f"{where}: pin {next(p for p in pins if None in nets[p])!r} "
                             "is not connected to any net")
        if len(found) > 1:
            by_net = {n: [p for p in pins if n in nets[p]] for n in sorted(found)}
            raise SetupError(f"{where}: pins on different nets: "
                             + "; ".join(f"{n}: {', '.join(ps)}" for n, ps in by_net.items()))
        return found.pop()

    gnd = {}
    for g in setup.sources + setup.loads:
        gnd[g.name] = net_of(g.gnd_pins, f"{g.name!r} gnd_pins")
    if len(set(gnd.values())) > 1:
        raise SetupError("gnd_pins are on different nets: "
                         + "; ".join(f"{name!r} on {net!r}" for name, net in gnd.items()))
    gnd_net = next(iter(gnd.values()))
    rails = {}
    for s in setup.sources:
        net = net_of(s.power_pins, f"{s.name!r} power_pins")
        if net == gnd_net:
            raise SetupError(f"{s.name!r}: power_pins are on the GND net {gnd_net!r}")
        if net in rails:
            raise SetupError(f"two sources on the power net {net!r} ({rails[net][0].name!r} and "
                             f"{s.name!r}); one source per power net")
        rails[net] = (s, [])
    for load in setup.loads:
        net = net_of(load.power_pins, f"{load.name!r} power_pins")
        if net not in rails:
            raise SetupError(f"{load.name!r}: its power_pins are on {net!r}, which has no source")
        rails[net][1].append(load)
    return BoundSetup(setup, gnd_net, tuple(Rail(net, gnd_net, s, tuple(ls))
                                            for net, (s, ls) in rails.items()))


def _natural(label):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", label)]


def setup_template(board, board_name, *, min_pins=3, width=88):
    """A setup file to fill in, listing the board's nets with at least ``min_pins`` pins."""
    pins = {}
    for pad in board.pads:
        if pad.net_id is not None:
            pins.setdefault(pad.net_id, set()).add(f"{pad.component_ref}.{pad.pin_number}")
    listed = sorted((n for n in pins if len(pins[n]) >= min_pins),
                    key=lambda n: (-len(pins[n]), n))
    lines = [
        f"# PDNLab DC setup for {board_name}. Fill in the blanks, delete what you do not need.",
        "# A source holds its power pins at 'volts'; a load draws 'amps'. Each lists its",
        "# power pins and its GND pins; the pins of one list are joined (one group).",
        "# Several rails: add one [[source]] per power net and its [[load]]s.",
        "",
        f"board = {board_name!r}".replace("'", '"'),
        "plating_um = 0                    # REQUIRED: barrel plating from your fab, e.g. 25",
        "",
        "[[source]]",
        'name = ""                         # e.g. "regulator"',
        "volts = 0                         # REQUIRED, e.g. 3.3",
        "power_pins = []                   # e.g. [\"U5.2\", \"U5.3\"]",
        "gnd_pins = []                     # e.g. [\"U5.4\"]",
        "",
        "[[load]]",
        'name = ""                         # e.g. "MCU"',
        "amps = 0                          # REQUIRED, e.g. 0.8",
        "power_pins = []",
        "gnd_pins = []",
        "",
        "# [options]                       # all optional",
        "# resistivity_ohm_m = 1.724e-8     # copper at 20 C",
        "# barrel_resistivity_ohm_m = 1.724e-8",
        "# tolerance_uv = 10                # refinement check: 10 uV + 1 %",
        "# tolerance_percent = 1",
        "",
        f"# ---- Nets with at least {min_pins} pins, most pins first (copy pin names from here) ----",
    ]
    for net in listed:
        labels = sorted(pins[net], key=_natural)
        components = sorted({p.rpartition(".")[0] for p in labels}, key=_natural)
        lines.append(f"# {net} ({len(labels)} pins)")
        for title, items in (("pins", labels), ("components", components)):
            row = f"#   {title}:"
            for item in items:
                if len(row) + len(item) + 1 > width:
                    lines.append(row)
                    row = "#    "
                row += " " + item
            lines.append(row)
    left_out = len(pins) - len(listed)
    if left_out:
        lines.append(f"# ({left_out} nets with fewer than {min_pins} pins are not listed)")
    return "\n".join(lines) + "\n"

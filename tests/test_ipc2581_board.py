"""One BoardModel from an IPC-2581 export (ipc2581_board), checked against KiCad.

The three public fixture boards (arc_track, pad_shapes, thermal_relief) are read
whole, and every pin pad is compared with KiCad's own .kicad_pcb: component, pad
number, net, absolute position (footprint position and rotation applied here, not
by the exporter), and whether it sits on a plated hole of KiCad's drill size. The
rejections are tested by changing one record of a real reader result.
"""

import math
import re
from dataclasses import replace
from pathlib import Path

import pytest
from shapely.affinity import translate

from pdnlab_dc.board import BoardModel
from pdnlab_dc.ipc2581_board import SCOPE, assemble_board, read_ipc2581_board
from pdnlab_dc.ipc2581_copper import ASSUMPTIONS, PadPin, UnreadCount, read_ipc2581_copper

FIXTURES = Path(__file__).parent / "fixtures"
BOARDS = ("arc_track", "pad_shapes", "thermal_relief")


def board_files(name):
    folder = FIXTURES / name
    return next(folder.glob("*.xml")), next(folder.glob("*.kicad_pcb"))


def sexpr(text):
    """Nested lists from a KiCad S-expression file; strings unquoted."""
    stack, current = [], []
    for token in re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', text):
        if token == "(":
            stack.append(current)
            current = []
        elif token == ")":
            done, current = current, stack.pop()
            current.append(done)
        else:
            current.append(token[1:-1] if token.startswith('"') else token)
    return current[0]


def child(node, name):
    found = [n for n in node if isinstance(n, list) and n and n[0] == name]
    return found[0] if found else None


def kicad_pins(path):
    """{(reference, pad number, x mm, y mm): (net, drill mm or None)} for copper pads."""
    pins = {}
    for footprint in (n for n in sexpr(path.read_text()) if isinstance(n, list) and n[0] == "footprint"):
        at = child(footprint, "at")
        fx, fy, angle = float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0
        reference = next(p[2] for p in footprint if isinstance(p, list) and p[:2] == ["property", "Reference"])
        c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        for pad in (n for n in footprint if isinstance(n, list) and n[0] == "pad"):
            if pad[2] == "np_thru_hole":
                continue
            px, py = float(child(pad, "at")[1]), float(child(pad, "at")[2])
            # KiCad (Y down): a footprint turned by +angle turns its pads counter-clockwise
            # on screen, which in Y-down coordinates is this rotation.
            x, y = fx + c * px + s * py, fy - s * px + c * py
            net = child(pad, "net")
            drill = child(pad, "drill")
            key = (reference, pad[1], round(x, 6), round(y, 6))
            assert key not in pins
            pins[key] = (net[-1] if net else None, float(drill[1]) if drill else None)
    return pins


@pytest.mark.parametrize("name", BOARDS)
def test_every_pin_pad_matches_kicad_board_file(name):
    xml, pcb = board_files(name)
    board = read_ipc2581_board(xml)
    assert isinstance(board, BoardModel)
    expected = kicad_pins(pcb)
    drills = {d.id: d for d in board.drills}
    features = {f.id: f for f in board.features}
    ours = {}
    for pad in board.pads:
        x, y = (1000 * v for v in pad.position_m)     # metres -> mm, KiCad Y down
        key = (pad.component_ref, pad.pin_number, round(x, 6), round(y, 6))
        drill = drills[pad.drill_id] if pad.drill_id else None
        if drill is not None:
            assert (drill.kind, drill.plating) == ("pin", "plated"), pad.id
        ours[key] = (pad.net_id, round(drill.diameter_m * 1000, 6) if drill else None)
        layers = [features[f].layer_id for f in pad.copper_feature_ids]
        if drill is None:
            assert len(layers) == 1 and layers[0] in ("F.Cu", "B.Cu"), pad.id
        else:   # a through-hole pad: copper on every layer, in stackup order
            assert layers == [layer.id for layer in board.layers], pad.id
    assert ours == expected


def test_counts_on_the_three_public_boards():
    counts = {}
    for name in BOARDS:
        board = read_ipc2581_board(board_files(name)[0])
        counts[name] = (len(board.layers), len(board.nets), len(board.features), len(board.pads),
                        sum(p.drill_id is not None for p in board.pads), len(board.drills))
    assert counts == {"arc_track": (2, 2, 17, 4, 0, 0),
                      "pad_shapes": (4, 14, 54, 18, 2, 5),
                      "thermal_relief": (4, 2, 18, 4, 1, 3)}


def test_model_holds_every_feature_and_drill_of_the_copper_reader():
    xml = board_files("pad_shapes")[0]
    copper, board = read_ipc2581_copper(xml), read_ipc2581_board(xml)
    assert board.features == copper.fills + copper.tracks + copper.pads
    assert board.drills == copper.drills and board.nets == copper.nets
    assert board.layers == copper.copper_layers
    # Every pad copper feature belongs to exactly one pin; vias to none.
    in_pins = [f for p in board.pads for f in p.copper_feature_ids]
    assert sorted(in_pins) == sorted(f.id for f in copper.pads if f.kind == "pad")


def test_assumptions_state_scope_conventions_and_what_was_not_read():
    xml = board_files("thermal_relief")[0]
    board = read_ipc2581_board(xml)
    assumptions = board.source.assumptions
    assert assumptions[0] == SCOPE and "not cut" in SCOPE and "no barrel" in SCOPE
    assert assumptions[1:-1] == ASSUMPTIONS[1:]
    assert assumptions[-1].startswith("Layers not inspected") and "Edge.Cuts" in assumptions[-1]
    assert board.source.input_sha256 == read_ipc2581_copper(xml).source.input_sha256


# Rejections: one record of a real reader result changed.

@pytest.fixture
def thermal():
    return read_ipc2581_copper(board_files("thermal_relief")[0])


def with_pad(copper, feature_id, **changes):
    pads = tuple(replace(f, **changes) if f.id == feature_id else f for f in copper.pads)
    return replace(copper, pads=pads)


def test_unread_copper_raises(thermal):
    broken = replace(thermal, unread=(UnreadCount("teardrop", "F.Cu", "GND", 2),))
    with pytest.raises(ValueError, match="copper not read.*2 teardrop on F.Cu"):
        assemble_board(broken)


def test_pad_copper_on_two_nets_raises(thermal):
    broken = with_pad(thermal, "pad:In1.Cu:TP1.1", net_id="+3V3")
    with pytest.raises(ValueError, match="pin:TP1.1.*different nets"):
        assemble_board(broken)


def test_pad_copper_on_two_holes_raises(thermal):
    via_hole = next(d.id for d in thermal.drills if d.kind == "via")
    broken = with_pad(thermal, "pad:B.Cu:TP1.1", drill_id=via_hole)
    with pytest.raises(ValueError, match="pin:TP1.1.*more than one hole"):
        assemble_board(broken)


def test_copper_pad_on_nonplated_hole_raises(thermal):
    hole = next(d.id for d in thermal.drills if d.kind == "pin")
    drills = tuple(replace(d, kind="mechanical", plating="nonplated") if d.id == hole else d
                   for d in thermal.drills)
    with pytest.raises(ValueError, match=f"pin:TP1.1.*non-plated hole {hole}.*not supported yet"):
        assemble_board(replace(thermal, drills=drills))


def split_pad(copper, net):
    """R1.2 given a second pad 1 mm away (a pin with two pads), on ``net``."""
    original = next(f for f in copper.pads if f.id == "pad:F.Cu:R1.2")
    pin = next(p for p in copper.pad_pins if p.feature_id == original.id)
    extra = replace(original, id="pad:F.Cu:R1.2#2", net_id=net,
                    geometry=translate(original.geometry, 1e-3, 0))
    moved = PadPin(extra.id, pin.component_ref, pin.pin_number,
                   (pin.position_m[0] + 1e-3, pin.position_m[1]))
    return replace(copper, pads=copper.pads + (extra,), pad_pins=copper.pad_pins + (moved,))


def test_a_pin_with_two_pads_gives_two_pin_pads(thermal):
    board = assemble_board(split_pad(thermal, "GND"))
    pads = [p for p in board.pads if p.pin_key == ("R1", "2")]
    assert [p.id for p in pads] == ["pin:R1.2", "pin:R1.2#2"]
    assert [p.copper_feature_ids for p in pads] == [("pad:F.Cu:R1.2",), ("pad:F.Cu:R1.2#2",)]


def test_two_pads_of_one_pin_on_different_nets_raise(thermal):
    with pytest.raises(ValueError, match="conflicting nets for electrical pin"):
        assemble_board(split_pad(thermal, "+3V3"))

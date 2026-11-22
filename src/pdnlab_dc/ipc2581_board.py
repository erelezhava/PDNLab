"""Assemble one BoardModel from a KiCad IPC-2581 export.

Reads the file once with ``ipc2581_copper`` (which uses ``ipc2581_metadata`` for the
header and stackup) and adds what the copper reader does not: PinPad records. A
PinPad is one physical pad of a component pin: the pad copper of the same component
and pin at the same position, one feature per copper layer, with the hole it sits on.
A pin with several pads (a split pad, for example) gives several PinPads with the same
pin key; BoardModel checks that they share a net.

Raises ValueError, with the pad or feature named, when:
* the copper reader left copper unread (``unread`` not empty): the model would not be
  the whole board;
* the copper of one pad is on different nets on different layers, or sits on more than
  one hole;
* a component pad sits on a non-plated hole (a copper ring around a mounting hole).
  Not supported yet: decided by Erekle, 2026-11-21, to revisit when a board needs it.

Everything else is checked by BoardModel itself: unique IDs, known layers and nets,
layer order, drill spans, and pin copper on the pin's net.

Holes are still not cut and no barrels are modelled; that is the cleanup and contact
model increments, with the setup's finished-hole size and plating.
"""

from dataclasses import replace
from pathlib import Path

from .board import BoardModel, PinPad
from .ipc2581_copper import ASSUMPTIONS as COPPER_ASSUMPTIONS, parse_ipc2581_copper

SCOPE = ("Whole copper of one KiCad IPC-2581 export as one BoardModel: stackup, nets, "
         "fills, tracks, pads, vias, drill holes and pin pads. Pad and via outlines are "
         "solid: holes are recorded and linked by drill_id, not cut, and no barrel or "
         "plating is modelled. Copper is not merged per net and layer.")


def read_ipc2581_board(path):
    """Read one file once; the same bytes are hashed and parsed."""
    return parse_ipc2581_board(Path(path).read_bytes())


def parse_ipc2581_board(data):
    return assemble_board(parse_ipc2581_copper(data))


def assemble_board(copper):
    """BoardModel from the copper reader's output; see the module docstring."""
    if copper.unread:
        listed = ", ".join(f"{u.count} {u.kind} on {u.layer_id}" for u in copper.unread)
        raise ValueError(f"copper not read, so the board would be incomplete: {listed}")
    pins = _pin_pads(copper)
    assumptions = (SCOPE,) + tuple(COPPER_ASSUMPTIONS[1:])
    if copper.uninspected_layers:
        assumptions += ("Layers not inspected (not copper): "
                        + ", ".join(copper.uninspected_layers) + ".",)
    source = replace(copper.source, assumptions=assumptions)
    return BoardModel(source, copper.copper_layers, copper.nets,
                      copper.fills + copper.tracks + copper.pads + copper.graphics, pins,
                      copper.drills,
                      copper.component_layers)


def _pin_pads(copper):
    features = {f.id: f for f in copper.pads}
    order = {layer.id: i for i, layer in enumerate(copper.copper_layers)}
    drills = {d.id: d for d in copper.drills}
    groups = {}
    for pin in copper.pad_pins:
        key = (pin.component_ref, pin.pin_number, pin.position_m)
        groups.setdefault(key, []).append(features[pin.feature_id])
    seen, pads = {}, []
    for (component, number, position), members in groups.items():
        members.sort(key=lambda f: order[f.layer_id])     # stackup order, top first
        n = seen[(component, number)] = seen.get((component, number), 0) + 1
        pad_id = f"pin:{component}.{number}" + ("" if n == 1 else f"#{n}")
        where = f"{pad_id} at ({position[0]:.6g}, {position[1]:.6g}) m"
        nets = {f.net_id for f in members}
        if len(nets) > 1:
            raise ValueError(f"{where}: pad copper on different nets on different layers: "
                             + ", ".join(f"{f.id} {f.net_id}" for f in members))
        holes = {f.drill_id for f in members} - {None}
        if len(holes) > 1:
            raise ValueError(f"{where}: pad copper on more than one hole: {sorted(holes)}")
        drill_id = holes.pop() if holes else None
        if drill_id is not None and drills[drill_id].kind == "mechanical":
            raise ValueError(f"{where}: copper pad on non-plated hole {drill_id} is not "
                             "supported yet")
        pads.append(PinPad(pad_id, component, number, nets.pop(), position,
                           tuple(f.id for f in members), drill_id))
    return tuple(pads)

"""One net of a real board as a solvable resistor network: sheets, barrels, pin contacts.

``build_net_network(board, clean, mesh, pins)`` takes the BoardModel, its cleaned copper
and one net's mesh and builds a Phase 1 ``Network``:

* one sheet per layer, sheet conductance = the layer's copper thickness (stackup) /
  resistivity;
* for every plated hole, a rim on each layer where the net has copper round it, and a
  barrel between neighbouring rims: length the distance between the layers' centres,
  copper from the finished radius d/2 to the drilled wall d/2 + plating;
* one equipotential contact for each requested pin, on the pad's whole copper (the mesh
  follows pad outlines): an SMD pad on its layer, a through-hole pin on the outer layer
  its component is mounted on. A hole whose edge lies inside a contact pad joins its
  barrel to that pad, which is equipotential anyway.

Decided by Erekle (2026-11-22, docs/DECISIONS.md): resistivity defaults to annealed
copper at 20 °C, 1.724e-8 Ω·m, separately for planes and barrels, both overridable
and stated in the result; pads are equipotential contacts; an island on a simulated
net is an error and nothing is simulated: after joining the net's pieces through the
barrels they must form one connected group.

Not done here: which pins are sources and loads (the setup file, increment 12),
temperature, solving, the refinement loop.
"""

from dataclasses import dataclass

import numpy as np

from .board import BoardModel
from .board_mesh import NetMesh
from .copper_cleanup import CleanCopper
from .network import Network
from .vias import barrel_resistance

COPPER_RESISTIVITY_OHM_M = 1.724e-8    # annealed copper (IACS) at 20 °C


@dataclass(frozen=True)
class PinContact:
    """An equipotential pad contact: pin label, the PinPad, its layer, the terminal."""
    pin: str
    pad_id: str
    layer_id: str
    terminal: str


@dataclass(frozen=True)
class BarrelLink:
    """One barrel resistor between the rims of a plated hole on two layers."""
    name: str
    drill_id: str
    upper_layer_id: str
    lower_layer_id: str
    resistance_ohm: float


@dataclass(frozen=True)
class NetNetwork:
    net_id: str
    network: Network
    contacts: dict              # pin label -> PinContact
    barrels: tuple[BarrelLink, ...]
    sheet_conductance_s: dict   # layer id -> siemens
    resistivity_ohm_m: float
    barrel_resistivity_ohm_m: float
    plating_m: float
    assumptions: tuple[str, ...]


class IslandError(ValueError):
    """Copper of a simulated net that is not joined to the rest of it."""


def net_pins(board, net_id):
    """Pin labels ('R1.2') of a net, for choosing sources and loads."""
    return sorted({f"{p.component_ref}.{p.pin_number}" for p in board.pads if p.net_id == net_id})


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or \
            not np.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return float(value)


def _where(geometry):
    p = geometry.representative_point()
    return f"({1000 * p.x:.3f}, {1000 * p.y:.3f}) mm"


def _check_islands(clean, mesh, rim_layers, cut):
    """Raise IslandError unless the net's pieces, joined by barrels, are one group."""
    pieces = {p.id: p for p in clean.pieces if p.net_id == mesh.net_id}
    parent = {pid: pid for pid in pieces}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for drill_id, layers in rim_layers.items():
        ends = [cut[(drill_id, layer)] for layer in layers]
        for a, b in zip(ends, ends[1:]):
            parent[root(a)] = root(b)
    groups = {}
    for pid in pieces:
        groups.setdefault(root(pid), []).append(pieces[pid])
    if len(groups) == 1:
        return
    ordered = sorted(groups.values(), key=lambda g: -sum(p.geometry.area for p in g))
    lines = []
    for group in ordered[1:]:
        biggest = max(group, key=lambda p: p.geometry.area)
        layers = sorted({p.layer_id for p in group})
        lines.append(f"{len(group)} piece(s) on {', '.join(layers)}, "
                     f"{sum(p.geometry.area for p in group) * 1e6:.4f} mm², "
                     f"at {biggest.layer_id} {_where(biggest.geometry)}")
    raise IslandError(f"net {mesh.net_id!r}: {len(ordered) - 1} island(s) not joined to the "
                      f"rest of the net through copper, vias or pin barrels; nothing simulated: "
                      + "; ".join(lines[:10]) + ("; ..." if len(lines) > 10 else ""))


def build_net_network(board, clean, mesh, pins=(), *, resistivity=COPPER_RESISTIVITY_OHM_M,
                      barrel_resistivity=None):
    """NetNetwork for ``mesh``'s net, with an equipotential contact for each pin label."""
    if not isinstance(board, BoardModel):
        raise ValueError("board must be a BoardModel")
    if not isinstance(clean, CleanCopper):
        raise ValueError("clean must be the CleanCopper of this board")
    if not isinstance(mesh, NetMesh):
        raise ValueError("mesh must be a NetMesh")
    rho = _positive(resistivity, "resistivity")
    rho_barrel = rho if barrel_resistivity is None else _positive(barrel_resistivity,
                                                                  "barrel_resistivity")
    net = mesh.net_id
    layers = {layer.id: layer for layer in board.layers}
    order = {layer.id: i for i, layer in enumerate(board.layers)}
    drills = {d.id: d for d in board.drills}
    sides = dict(board.component_layers)
    meshes = {L.layer_id: L for L in mesh.layers}
    cut = {(h.drill_id, h.layer_id): h.piece_ids[0] for h in clean.cut_holes
           if len(h.piece_ids) == 1}

    # Plated holes of this net and the layers where they have a rim, in stack order.
    rim_layers = {}
    for L in sorted(mesh.layers, key=lambda L: order[L.layer_id]):
        for drill_id in L.hole_rims:
            if drills[drill_id].plating == "plated":
                rim_layers.setdefault(drill_id, []).append(L.layer_id)
    rim_layers = {d: ls for d, ls in rim_layers.items() if len(ls) >= 2}
    _check_islands(clean, mesh, rim_layers, cut)

    network = Network()
    conductance = {}
    for L in mesh.layers:
        conductance[L.layer_id] = layers[L.layer_id].thickness_m / rho
        network.add_sheet(L.layer_id, L.nodes, L.triangles, conductance[L.layer_id])

    # Pin contacts: the whole pad copper on its contact layer.
    contacts, contact_nodes = {}, {}
    for label in pins:
        component, _, number = str(label).rpartition(".")
        mine = [p for p in board.pads if (p.component_ref, p.pin_number) == (component, number)]
        if not mine:
            raise ValueError(f"pin {label!r}: no such pin on the board")
        if len(mine) > 1:
            raise ValueError(f"pin {label!r}: a pin with {len(mine)} pads is not supported yet")
        (pad,) = mine
        if pad.net_id != net:
            raise ValueError(f"pin {label!r} is on net {pad.net_id!r}, not {net!r}")
        features = {f.id: f for f in board.features if f.id in pad.copper_feature_ids}
        if pad.drill_id is None:
            (feature,) = features.values()
        else:
            if pad.component_ref not in sides:
                raise ValueError(f"pin {label!r}: the side its component is mounted on is unknown")
            on_side = [f for f in features.values() if f.layer_id == sides[pad.component_ref]]
            if len(on_side) != 1:
                raise ValueError(f"pin {label!r}: no pad copper on its component's side "
                                 f"{sides[pad.component_ref]}")
            (feature,) = on_side
        L = meshes.get(feature.layer_id)
        if L is None or feature.id not in L.pad_ids:
            raise ValueError(f"pin {label!r}: its pad {feature.id} is not in the mesh")
        nodes = np.unique(L.triangles[L.pad_of == L.pad_ids.index(feature.id)])
        if nodes.size == 0:
            raise ValueError(f"pin {label!r}: its pad {feature.id} has no triangles")
        contacts[str(label)] = PinContact(str(label), pad.id, feature.layer_id, f"pin:{label}")
        contact_nodes[str(label)] = (feature.layer_id, nodes)

    # Rim endpoints: a rim lying inside a contact pad joins through that pad's terminal.
    endpoint, joined = {}, set()
    for drill_id, rim_list in rim_layers.items():
        for layer_id in rim_list:
            rim = meshes[layer_id].hole_rims[drill_id]
            for label, (contact_layer, nodes) in contact_nodes.items():
                if contact_layer != layer_id:
                    continue
                inside = np.isin(rim, nodes)
                if inside.all():
                    endpoint[(drill_id, layer_id)] = contacts[label].terminal
                    joined.add(label)
                elif inside.any():
                    raise ValueError(f"{drill_id} on {layer_id}: its edge lies partly inside "
                                     f"the pad of pin {label!r}; not supported")
    for label, (layer_id, nodes) in contact_nodes.items():
        network.add_terminal(contacts[label].terminal, layer_id, nodes, rim=label in joined)
    for drill_id, rim_list in rim_layers.items():
        for layer_id in rim_list:
            if (drill_id, layer_id) not in endpoint:
                name = f"rim:{drill_id}:{layer_id}"
                network.add_terminal(name, layer_id, meshes[layer_id].hole_rims[drill_id], rim=True)
                endpoint[(drill_id, layer_id)] = name

    barrels = []
    for drill_id, rim_list in rim_layers.items():
        d = drills[drill_id]
        inner, outer = d.diameter_m / 2, d.diameter_m / 2 + clean.plating_m
        for upper, lower in zip(rim_list, rim_list[1:]):
            length = layers[lower].z_m - layers[upper].z_m
            name = f"barrel:{drill_id}:{upper}-{lower}"
            network.add_barrel(name, endpoint[(drill_id, upper)], endpoint[(drill_id, lower)],
                               length=length, inner_radius=inner, outer_radius=outer,
                               resistivity=rho_barrel)
            barrels.append(BarrelLink(name, drill_id, upper, lower,
                                      barrel_resistance(length, inner, outer, rho_barrel)))
    assumptions = (
        f"Sheet resistivity {rho:.6g} Ω·m and barrel resistivity {rho_barrel:.6g} Ω·m "
        "(default: annealed copper at 20 °C); no temperature dependence.",
        f"Barrels: plating {clean.plating_m * 1e6:.6g} µm, copper from the finished radius "
        "to the drilled wall, length between layer centres; no contact or 3D spreading "
        "resistance at the rims.",
        "Pin contacts are equipotential over the whole pad; through-hole pins on their "
        "component's side.",
    )
    return NetNetwork(net, network, contacts, tuple(barrels), conductance, rho, rho_barrel,
                      clean.plating_m, assumptions)

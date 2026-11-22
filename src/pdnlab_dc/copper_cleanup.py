"""Copper cleanup: merge each net's copper per layer into pieces and cut the holes.

``clean_copper(board, plating_m)`` takes a BoardModel (from ``ipc2581_board``) and
returns ``CleanCopper``: separate pieces of copper per layer and net, each one valid
Polygon with its holes cut, and a record of every hole cut on every layer. Decided by
Erekle (2026-11-21 and 2026-11-22, see docs/DECISIONS.md):

* KiCad's hole value d is the finished hole. A plated hole is cut at d/2 + t, the
  drilled wall, with t the plating thickness (``plating_m``, required, no default);
  the contact model adds the barrel from d/2 to d/2 + t. A non-plated hole is cut at
  d/2. Holes are polygons with every vertex on the true circle and at most 1 µm chord
  error, the rule used for tracks and pads.
* Copper of two different nets overlapping on one layer raises: it is a short (net
  ties too, not supported yet). Copper with no net is its own group, net_id None, kept
  but outside the DC solve; overlapping net copper raises, because its net is unknown.
  A plated hole whose drilled wall reaches copper of another net raises for the same
  reason.
* A plated hole must lie inside its own net's copper on every layer where it has pad
  or via copper; otherwise the annular ring breaks out and the barrel would attach to
  a broken rim.
* Several pieces per net and layer are normal (they join through vias and planes).
  Whether a piece is an island is decided by the contact model, once barrels join the
  layers: an island on a simulated net is an error there.
* Nothing is repaired, simplified or dropped silently: an invalid piece or a piece
  under 1 µm² raises. A feature lying entirely inside a hole (a track stub ending at a
  via or pin centre) is drilled away: it is listed in ``removed`` with that hole. Any
  other feature that ends up in no piece raises.

Not done here: barrels, contacts, islands, meshing, setup, solve.
"""

import math
from dataclasses import dataclass

from shapely import STRtree
from shapely.affinity import translate
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.validation import explain_validity

from .board import BoardModel
from .pad_outline import circle_outline

HOLE_TOL_M = 1e-6          # chord error of hole outlines, as for tracks and pads
AREA_LIMIT_M2 = 1e-12      # 1 µm²: overlaps, breakouts and pieces below this are not counted

SCOPE = ("Copper merged per net and layer into separate pieces, holes cut (plated at "
         "finished radius + plating, non-plated at their radius). No barrels, contacts "
         "or island check: pieces of one net are not yet joined across layers.")


@dataclass(frozen=True)
class CopperPiece:
    """One separate piece of copper on one layer, after merging and cutting."""
    id: str
    layer_id: str
    net_id: str | None
    geometry: Polygon
    feature_ids: tuple[str, ...]
    drill_ids: tuple[str, ...]


@dataclass(frozen=True)
class CutHole:
    """One hole cut on one layer: the radius used and the pieces whose edge it is."""
    drill_id: str
    layer_id: str
    radius_m: float
    piece_ids: tuple[str, ...]


@dataclass(frozen=True)
class RemovedFeature:
    """A feature lying entirely inside a hole on its layer: drilled away."""
    feature_id: str
    drill_id: str


@dataclass(frozen=True)
class CleanCopper:
    pieces: tuple[CopperPiece, ...]
    cut_holes: tuple[CutHole, ...]
    removed: tuple[RemovedFeature, ...]
    plating_m: float
    assumptions: tuple[str, ...]


def _where(geometry):
    p = geometry.representative_point()
    return f"({1000 * p.x:.3f}, {1000 * p.y:.3f}) mm"


def _net(net):
    return "no net" if net is None else repr(net)


def _polygons(geometry):
    """Polygon parts of a geometry; parts with no area (points, lines) are not copper."""
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    return [p for g in geometry.geoms for p in _polygons(g)]


def clean_copper(board, plating_m):
    """CleanCopper for ``board``; see the module docstring for what raises."""
    if not isinstance(board, BoardModel):
        raise ValueError("board must be a BoardModel")
    if (isinstance(plating_m, bool) or not isinstance(plating_m, (int, float))
            or not math.isfinite(plating_m) or plating_m <= 0):
        raise ValueError("plating_m (plating thickness in metres, from the fabricator) is "
                         "required and must be a positive number")
    order = {layer.id: i for i, layer in enumerate(board.layers)}
    drill_nets = {}
    for f in board.features:
        if f.drill_id is not None:
            drill_nets.setdefault(f.drill_id, set()).add(f.net_id)
    for drill_id, nets in drill_nets.items():
        if len(nets) > 1:
            raise ValueError(f"{drill_id}: its pad and via copper is on more than one net: "
                             + ", ".join(sorted(_net(n) for n in nets)))
    pieces, cut_holes, removed = [], [], []
    for layer in board.layers:
        features = [f for f in board.features if f.layer_id == layer.id]
        _check_overlaps(layer.id, features)
        nets = {}
        for f in features:
            nets.setdefault(f.net_id, []).append(f)
        merged = {net: unary_union([f.geometry for f in group]) for net, group in nets.items()}
        holes = _holes(board, layer.id, order, plating_m, drill_nets, merged)
        cut = unary_union([h for _, h, _ in holes]) if holes else Polygon()
        layer_pieces = []
        for net in sorted(merged, key=lambda n: (n is not None, n or "")):
            parts = _polygons(merged[net].difference(cut)) if holes else _polygons(merged[net])
            parts.sort(key=lambda p: (-p.area, p.representative_point().x,
                                      p.representative_point().y))
            for k, part in enumerate(parts):
                piece_id = f"piece:{layer.id}:{'none' if net is None else net}:{k}"
                if not part.is_valid:
                    raise ValueError(f"{piece_id} at {_where(part)}: invalid copper after "
                                     f"merging and cutting: {explain_validity(part)}")
                if part.area < AREA_LIMIT_M2:
                    raise ValueError(f"{piece_id} at {_where(part)}: piece of "
                                     f"{part.area * 1e12:.3g} µm² left after merging and "
                                     "cutting")
                layer_pieces.append((piece_id, net, part))
        members = {piece_id: [] for piece_id, _, _ in layer_pieces}
        tree = STRtree([part for _, _, part in layer_pieces]) if layer_pieces else None
        for f in features:
            placed = False
            for i in (tree.query(f.geometry) if tree is not None else ()):
                piece_id, net, part = layer_pieces[i]
                if net == f.net_id and part.intersection(f.geometry).area > AREA_LIMIT_M2:
                    members[piece_id].append(f.id)
                    placed = True
            if not placed:
                inside = [d for d, hole, _ in holes
                          if f.geometry.difference(hole).area <= AREA_LIMIT_M2]
                if not inside:
                    raise ValueError(f"{f.id} at {_where(f.geometry)}: copper in no piece after "
                                     "merging and cutting, and not inside a hole")
                removed.append(RemovedFeature(f.id, inside[0]))
        if not layer_pieces:
            continue
        edges = {piece_id: [] for piece_id, _, _ in layer_pieces}
        for drill_id, hole, radius in holes:
            rim = hole.exterior.buffer(HOLE_TOL_M)
            touching = tuple(layer_pieces[i][0] for i in tree.query(rim)
                             if layer_pieces[i][2].intersection(rim).area > AREA_LIMIT_M2)
            if touching:
                cut_holes.append(CutHole(drill_id, layer.id, radius, touching))
                for piece_id in touching:
                    edges[piece_id].append(drill_id)
        for piece_id, net, part in layer_pieces:
            pieces.append(CopperPiece(piece_id, layer.id, net, part, tuple(members[piece_id]),
                                      tuple(edges[piece_id])))
    assumptions = (SCOPE, f"Plating thickness {plating_m * 1e6:.6g} µm, board-wide.",
                   "Hole outlines have every vertex on the true circle, at most 1 µm chord "
                   "error, so a hole edge keeps at most 1 µm of extra copper.")
    return CleanCopper(tuple(pieces), tuple(cut_holes), tuple(removed), float(plating_m),
                       assumptions)


def _check_overlaps(layer_id, features):
    """Raise for copper of two nets (no net counts as its own) overlapping on a layer."""
    if not features:
        return
    tree = STRtree([f.geometry for f in features])
    for i, j in zip(*tree.query([f.geometry for f in features], predicate="intersects")):
        a, b = features[i], features[j]
        if i < j and a.net_id != b.net_id:
            overlap = a.geometry.intersection(b.geometry)
            if overlap.area > AREA_LIMIT_M2:
                tie = " (a net tie is not supported yet)" if None not in (a.net_id, b.net_id) else ""
                raise ValueError(f"{layer_id}: copper of {_net(a.net_id)} ({a.id}) and "
                                 f"{_net(b.net_id)} ({b.id}) overlaps by "
                                 f"{overlap.area * 1e12:.3g} µm² at {_where(overlap)}: a short"
                                 + tie)


def _holes(board, layer_id, order, plating_m, drill_nets, merged):
    """(drill id, hole polygon, radius) for every hole through this layer, after checks."""
    holes = []
    linked = {f.drill_id for f in board.features if f.layer_id == layer_id and f.drill_id}
    net_list = list(merged)
    tree = STRtree([merged[n] for n in net_list])
    for drill in board.drills:
        if not order[drill.start_layer_id] <= order[layer_id] <= order[drill.end_layer_id]:
            continue
        plated = drill.plating == "plated"
        if not plated and drill.plating != "nonplated":
            raise ValueError(f"{drill.id}: plating {drill.plating!r}; plated or nonplated "
                             "is needed to choose the cut radius")
        radius = drill.diameter_m / 2 + (plating_m if plated else 0.0)
        hole = translate(circle_outline(2 * radius, HOLE_TOL_M), *drill.position_m)
        if plated:
            own = drill_nets.get(drill.id, {None})
            (net,) = own
            for i in tree.query(hole):
                other, copper = net_list[i], merged[net_list[i]]
                if other != net and hole.intersection(copper).area > AREA_LIMIT_M2:
                    raise ValueError(f"{layer_id}: {drill.id} of {_net(net)} reaches copper "
                                     f"of {_net(other)} at its drilled wall (radius "
                                     f"{radius * 1e3:.4g} mm): a short")
            if drill.id in linked:
                missing = hole.difference(merged[net])
                if missing.area > AREA_LIMIT_M2:
                    raise ValueError(f"{layer_id}: {drill.id} of {_net(net)} breaks out of "
                                     f"its copper (drilled radius {radius * 1e3:.4g} mm, "
                                     f"{missing.area * 1e12:.3g} µm² outside) at "
                                     f"{_where(missing)}")
        holes.append((drill.id, hole, radius))
    return holes

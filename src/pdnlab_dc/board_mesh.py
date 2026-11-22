"""Mesh one net of a cleaned board, layer by layer, with Gmsh (external program).

``mesh_net(clean, net_id, sizes)`` meshes the copper pieces of one net on each copper
layer into linear triangles and returns ``NetMesh``: per layer, nodes in metres,
triangles, the piece each triangle belongs to, and for every plated hole the nodes on
its edge (where the contact model attaches the barrel). Decided by Erekle
(2026-11-22, docs/DECISIONS.md):

* Only the nets being simulated are meshed.
* The user does not choose element sizes. ``MeshSizes`` holds the default rules and one
  ``scale`` factor; the refinement check (increment 13) solves at scale 1, then 0.5 ...
  Sizes are, at every place, the smallest of:
  - the maximum, 0.5 mm × scale;
  - the outline's own spacing at each vertex (× scale);
  - around every plated hole: scale × (the hole outline's side + growth × distance), with
    growth 0.075 m/m, up to the maximum: halving the scale halves the size at every
    distance;
  - around every pad without a hole (SMD pads, where current enters and leaves): the
    same rule from the pad's edge, starting at a quarter of its shorter side
    (``per_width``), at most the maximum. Measured 2026-11-22: without it, a pad fed
    through 0.3 mm spokes was 2.2 % low at scale 1;
  - thin copper: in any part of a piece narrower than w, at most w / ``per_width``
    (4: four triangles across a spoke, neck or track). Narrow parts are found by
    opening each piece (shrink by w/2, grow by w/2) at widths 4 × maximum, then
    smaller by a factor of √2, and keeping what is removed when it is elongated (area
    at least w²/2), so that the rounding of convex corners does not count as thin.
* Gmsh runs as an external program on a temporary folder, deleted afterwards unless
  ``keep_dir`` is given.

Not done here: contacts, barrels, islands, solving, the refinement loop.
"""

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import meshio
import numpy as np
from shapely import STRtree
from shapely.geometry import Point, Polygon

from .board import BoardModel
from .copper_cleanup import CleanCopper
from .gmsh_mesh import run_gmsh


@dataclass(frozen=True)
class MeshSizes:
    """Size rules. Lengths in metres; growth in metres of size per metre of distance."""
    max_m: float = 0.5e-3
    growth: float = 0.075
    per_width: float = 4.0
    scale: float = 1.0

    def __post_init__(self):
        for name in ("max_m", "growth", "per_width", "scale"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                    not math.isfinite(value) or value <= 0:
                raise ValueError(f"MeshSizes.{name} must be a positive number")
        if self.growth > 1:
            raise ValueError("MeshSizes.growth must be at most 1")
        if self.per_width < 1:
            raise ValueError("MeshSizes.per_width must be at least 1")


@dataclass(frozen=True)
class LayerMesh:
    """One net on one layer: the sheet the solver assembles."""
    layer_id: str
    nodes: np.ndarray            # (N, 2) metres
    triangles: np.ndarray        # (M, 3) node indices
    piece_of: np.ndarray         # (M,) index into piece_ids
    piece_ids: tuple[str, ...]
    hole_rims: dict              # plated drill id -> node indices on its edge


@dataclass(frozen=True)
class NetMesh:
    net_id: str
    layers: tuple[LayerMesh, ...]
    sizes: MeshSizes
    gmsh_version: str
    seconds: float


def _ring_points(ring):
    return np.asarray(ring.coords)[:-1]


def _spacing(points):
    """Average length of the two outline segments at each vertex."""
    seg = np.hypot(*(points - np.roll(points, -1, axis=0)).T)
    return 0.5 * (seg + np.roll(seg, 1))


def _thin_regions(piece, sizes):
    """[(width w, region narrower than w)], widest first, for the thin-copper rule."""
    regions = []
    w = sizes.per_width * sizes.max_m * sizes.scale
    smallest = 2e-6
    while w / sizes.per_width > smallest:
        opened = piece.buffer(-w / 2, quad_segs=8).buffer(w / 2, quad_segs=8)
        removed = piece.difference(opened)
        parts = [g for g in getattr(removed, "geoms", [removed])
                 if not g.is_empty and g.area >= 0.5 * w * w]
        regions.append((w, parts))
        w /= math.sqrt(2)
    return regions


def _point_sizes(piece, sizes):
    """Size at every outline vertex of one piece: [(ring, points, sizes)]."""
    rings = [piece.exterior, *piece.interiors]
    out = []
    regions = _thin_regions(piece, sizes)
    for ring in rings:
        points = _ring_points(ring)
        lc = np.minimum(_spacing(points), sizes.max_m) * sizes.scale
        for w, parts in regions:
            if not parts:
                continue
            tree = STRtree(parts)
            cap = w / sizes.per_width
            for i, (x, y) in enumerate(points):
                if lc[i] <= cap:
                    continue
                p = Point(x, y)
                if any(parts[j].distance(p) <= 1e-7 for j in tree.query(p.buffer(1e-7))):
                    lc[i] = cap
        out.append((ring, points, lc))
    return out


def _hole_ring(piece, cut, drills):
    """{interior ring index: drill id} for the plated holes cut from this piece."""
    found = {}
    for drill_id in cut:
        drill = drills[drill_id]
        if drill.plating != "plated":
            continue
        centre = Point(drill.position_m)
        matches = [k for k, ring in enumerate(piece.interiors) if Polygon(ring).contains(centre)
                   and Polygon(ring).area < math.pi * (drill.diameter_m) ** 2]
        if len(matches) != 1:
            raise ValueError(f"{drill_id}: expected one hole edge in its piece, found "
                             f"{len(matches)}")
        found[matches[0]] = drill_id
    return found


def _pad_edge(pad, sizes):
    """Element size at the edge of a pad without a hole: a quarter of its shorter side."""
    rect = pad.minimum_rotated_rectangle
    coords = np.asarray(rect.exterior.coords)
    sides = np.hypot(*np.diff(coords[:3], axis=0).T)
    return min(sizes.max_m, float(sides.min()) / sizes.per_width)


def _write_geo(path, pieces, drills, pads, sizes):
    """Write the .geo for one layer; return {physical name: what it stands for}."""
    lines = [f"// PDNLab board mesh, metres. scale={sizes.scale:.6g}",
             f"Mesh.MeshSizeMax = {sizes.max_m * sizes.scale:.9g};",
             "Mesh.MeshSizeMin = 1e-7;",
             "Mesh.MeshSizeExtendFromBoundary = 0;",
             "Mesh.MeshSizeFromPoints = 1;",
             "Mesh.MeshSizeFromCurvature = 0;",
             "Mesh.Algorithm = 6;"]
    pid = lid = cid = sid = 0
    names = {}
    rules = {}              # edge size (µm, rounded) -> {"curves": [...], "points": [...]}
    for k, piece in enumerate(pieces):
        holes = _hole_ring(piece.geometry, piece.drill_ids, drills)
        loops = []
        for r, (ring, points, lc) in enumerate(_point_sizes(piece.geometry, sizes)):
            first = pid + 1
            for (x, y), size in zip(points, lc):
                pid += 1
                lines.append(f"Point({pid}) = {{{x:.12g}, {y:.12g}, 0, {size:.9g}}};")
            ring_lines = []
            for i in range(len(points)):
                lid += 1
                a, b = first + i, first + (i + 1) % len(points)
                lines.append(f"Line({lid}) = {{{a}, {b}}};")
                ring_lines.append(lid)
            cid += 1
            lines.append(f"Curve Loop({cid}) = {{{', '.join(map(str, ring_lines))}}};")
            loops.append(cid)
            if r > 0 and (r - 1) in holes:
                name = f"rim_{len(names)}"
                names[name] = ("rim", holes[r - 1])
                side = float(np.mean(_spacing(points)))
                rules.setdefault(round(side * 1e6), {"curves": [], "points": []})["curves"] += ring_lines
                lines.append(f'Physical Curve("{name}") = {{{", ".join(map(str, ring_lines))}}};')
        sid += 1
        lines.append(f"Plane Surface({sid}) = {{{', '.join(map(str, loops))}}};")
        name = f"piece_{k}"
        names[name] = ("piece", piece.id)
        lines.append(f'Physical Surface("{name}") = {{{sid}}};')
    for pad in pads:
        edge = _pad_edge(pad.geometry, sizes)
        ring = pad.geometry.exterior if pad.geometry.geom_type == "Polygon" else \
            max(pad.geometry.geoms, key=lambda g: g.area).exterior
        n = max(8, int(math.ceil(ring.length / edge)))
        rule = rules.setdefault(round(edge * 1e6), {"curves": [], "points": []})
        for i in range(n):
            x, y = ring.interpolate(i / n, normalized=True).coords[0]
            pid += 1
            lines.append(f"Point({pid}) = {{{x:.12g}, {y:.12g}, 0, {sizes.max_m * sizes.scale:.9g}}};")
            rule["points"].append(pid)
    fields = []
    for n, (edge_um, rule) in enumerate(sorted(rules.items())):
        dist, thr = 2 * n + 1, 2 * n + 2
        edge = edge_um * 1e-6 * sizes.scale
        top = sizes.max_m * sizes.scale
        lines.append(f"Field[{dist}] = Distance;")
        if rule["curves"]:
            lines.append(f"Field[{dist}].CurvesList = {{{', '.join(map(str, rule['curves']))}}};")
            lines.append(f"Field[{dist}].Sampling = 20;")
        if rule["points"]:
            lines.append(f"Field[{dist}].PointsList = {{{', '.join(map(str, rule['points']))}}};")
        lines += [
                  f"Field[{thr}] = Threshold;",
                  f"Field[{thr}].InField = {dist};",
                  f"Field[{thr}].SizeMin = {edge:.9g};",
                  f"Field[{thr}].SizeMax = {top:.9g};",
                  f"Field[{thr}].DistMin = 0;",
                  f"Field[{thr}].DistMax = {max(top - edge, 0) / (sizes.growth * sizes.scale):.9g};"]
        fields.append(thr)
    if fields:
        m = 2 * len(rules) + 1
        lines += [f"Field[{m}] = Min;", f"Field[{m}].FieldsList = {{{', '.join(map(str, fields))}}};",
                  f"Background Field = {m};"]
    Path(path).write_text("\n".join(lines) + "\n")
    return names


def _read(msh, names, layer_id):
    mesh = meshio.read(msh)
    tags = mesh.cell_data_dict.get("gmsh:physical", {})
    tri_all, tri_tag = mesh.cells_dict.get("triangle"), tags.get("triangle")
    if tri_all is None or tri_tag is None:
        raise RuntimeError(f"{layer_id}: Gmsh produced no tagged triangles")
    by_tag = {int(tag): name for name, (tag, dim) in mesh.field_data.items()}
    pieces = [n for n in names if names[n][0] == "piece"]
    piece_index = {n: i for i, n in enumerate(pieces)}
    keep = np.array([by_tag.get(int(t)) in piece_index for t in tri_tag])
    triangles, piece_of = tri_all[keep], np.array(
        [piece_index[by_tag[int(t)]] for t in tri_tag[keep]], dtype=np.int64)
    for name in pieces:
        if not np.any(piece_of == piece_index[name]):
            raise RuntimeError(f"{layer_id}: {names[name][1]} got no triangles")
    used = np.unique(triangles)
    mapping = np.full(len(mesh.points), -1, dtype=np.int64)
    mapping[used] = np.arange(len(used))
    rims = {}
    line_all, line_tag = mesh.cells_dict.get("line"), tags.get("line")
    for name, (kind, drill_id) in names.items():
        if kind != "rim":
            continue
        tag = mesh.field_data[name][0]
        nodes = np.unique(line_all[line_tag == tag])
        if nodes.size == 0 or np.any(mapping[nodes] < 0):
            raise RuntimeError(f"{layer_id}: edge of {drill_id} not on the mesh")
        rims[drill_id] = mapping[nodes]
    return (mesh.points[used, :2].copy(), mapping[triangles], piece_of,
            tuple(names[n][1] for n in pieces), rims)


def mesh_net(clean, board, net_id, sizes=MeshSizes(), gmsh="gmsh", keep_dir=None,
             timeout=600):
    """NetMesh of one net of ``board`` (the BoardModel ``clean`` was made from)."""
    if not isinstance(clean, CleanCopper):
        raise ValueError("clean must be a CleanCopper")
    if not isinstance(board, BoardModel):
        raise ValueError("board must be the BoardModel the copper was cleaned from")
    drills = board.drills
    if not isinstance(sizes, MeshSizes):
        raise ValueError("sizes must be MeshSizes")
    pieces = [p for p in clean.pieces if p.net_id == net_id]
    if net_id is None or not pieces:
        raise ValueError(f"net {net_id!r} has no copper pieces to mesh")
    drill_map = {d.id: d for d in (drills or ())}
    missing = {d for p in pieces for d in p.drill_ids} - set(drill_map)
    if missing:
        raise ValueError(f"drills needed for the holes of {net_id!r}: {sorted(missing)[:3]}")
    start = perf_counter()
    layers, version = [], ""
    order = []
    for p in pieces:
        if p.layer_id not in order:
            order.append(p.layer_id)
    context = tempfile.TemporaryDirectory() if keep_dir is None else None
    folder = Path(context.name if context else keep_dir)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        for n, layer_id in enumerate(order):
            mine = [p for p in pieces if p.layer_id == layer_id]
            geo, msh = folder / f"layer{n}.geo", folder / f"layer{n}.msh"
            pads = [f for f in board.features if f.layer_id == layer_id and f.net_id == net_id
                    and f.kind == "pad" and f.drill_id is None]
            names = _write_geo(geo, mine, drill_map, pads, sizes)
            version = run_gmsh(geo, msh, executable=gmsh, timeout=timeout)
            nodes, triangles, piece_of, piece_ids, rims = _read(msh, names, layer_id)
            layers.append(LayerMesh(layer_id, nodes, triangles, piece_of, piece_ids, rims))
    finally:
        if context:
            context.cleanup()
    return NetMesh(net_id, tuple(layers), sizes, version, perf_counter() - start)

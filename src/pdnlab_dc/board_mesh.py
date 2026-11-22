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
    same rule from the pad's edge, starting at its shorter side / ``pad_divisions``
    (16; about 50 µm on an 0603 pad), at most the maximum. Measured 2026-11-22 with
    the pad as an exact equipotential contact: without this rule a pad fed through
    0.3 mm spokes was 2.2 % low at scale 1, starting at a quarter of the side 1.8 %,
    at a sixteenth 0.7 %. Current crowds at an equipotential pad's edge, so it
    converges slowly (each halving halves the change);
  - thin copper: in any part of a piece narrower than w, at most w / ``per_width``
    (4: four triangles across a spoke, neck or track). Narrow parts are found by
    opening each piece (shrink by w/2, grow by w/2) at widths 4 × maximum, then
    smaller by a factor of √2, and keeping what is removed when it is elongated (area
    at least w²/2), so that the rounding of convex corners does not count as thin.
* The mesh follows every pad outline of the net, so each pad is exactly a set of whole
  triangles (``pad_of``) and can be an equipotential contact. Pieces and pads are
  rounded onto a 10 nm grid (``GRID_M``) and noded together at that precision, so
  that faces share their edge points exactly and no two points are closer than Gmsh
  can tell apart; the mesh covers each piece as rounded, at most a grid step of
  copper along its outline.
* Gmsh runs as an external program on a temporary folder, deleted afterwards unless
  ``keep_dir`` is given.

Not done here: contacts, barrels, islands, solving, the refinement loop.
"""

import math
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter

import meshio
import numpy as np
from shapely import STRtree
from shapely.geometry import Point, Polygon
from scipy.spatial import cKDTree
import shapely
from shapely import set_precision, union_all
from shapely.ops import polygonize, unary_union

from .board import BoardModel
from .copper_cleanup import CleanCopper
from .gmsh_mesh import run_gmsh


GRID_M = 1e-8          # pieces and pads rounded onto a 10 nm grid before meshing
AREA_MIN_M2 = 1e-12    # a face under 1 µm² after splitting along pads raises


@dataclass(frozen=True)
class MeshSizes:
    """Size rules. Lengths in metres; growth in metres of size per metre of distance."""
    max_m: float = 0.5e-3
    growth: float = 0.075
    per_width: float = 4.0
    pad_divisions: float = 16.0
    scale: float = 1.0

    def __post_init__(self):
        for name in ("max_m", "growth", "per_width", "pad_divisions", "scale"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                    not math.isfinite(value) or value <= 0:
                raise ValueError(f"MeshSizes.{name} must be a positive number")
        if self.growth > 1:
            raise ValueError("MeshSizes.growth must be at most 1")
        if self.per_width < 1:
            raise ValueError("MeshSizes.per_width must be at least 1")
        if self.pad_divisions < 1:
            raise ValueError("MeshSizes.pad_divisions must be at least 1")


@dataclass(frozen=True)
class LayerMesh:
    """One net on one layer: the sheet the solver assembles."""
    layer_id: str
    nodes: np.ndarray            # (N, 2) metres
    triangles: np.ndarray        # (M, 3) node indices
    piece_of: np.ndarray         # (M,) index into piece_ids
    piece_ids: tuple[str, ...]
    hole_rims: dict              # plated drill id -> node indices on its edge
    pad_of: np.ndarray           # (M,) index into pad_ids, -1 off pads
    pad_ids: tuple[str, ...]     # pad copper features of the net on this layer


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


def _cap_thin(points, lc, regions, sizes):
    """Apply the thin-copper rule to the sizes at ``points`` (in place)."""
    for w, parts in regions:
        if not parts:
            continue
        tree = STRtree(parts)
        cap = w / sizes.per_width
        for i, (x, y) in enumerate(points):
            if lc[i] <= cap:
                continue
            q = Point(x, y)
            if any(parts[j].distance(q) <= 1e-7 for j in tree.query(q.buffer(1e-7))):
                lc[i] = cap


def _hole_ring(piece, cut, drills):
    """{interior ring index: drill id} for the plated holes cut from this piece."""
    found = {}
    for drill_id in cut:
        drill = drills[drill_id]
        if drill.plating != "plated":
            continue
        centre = Point(drill.position_m)
        # The edge around the drill's centre, no larger than 4 times the hole's own area
        # (round or slot), so a larger opening around it is not taken for the hole.
        matches = [k for k, ring in enumerate(piece.interiors) if Polygon(ring).contains(centre)
                   and Polygon(ring).area < 4 * drill.area_m2]
        if len(matches) != 1:
            raise ValueError(f"{drill_id}: expected one hole edge in its piece, found "
                             f"{len(matches)}")
        found[matches[0]] = drill_id
    return found


def _pad_edge(pad, sizes):
    """Element size at the edge of a pad without a hole: its shorter side / pad_divisions."""
    rect = pad.minimum_rotated_rectangle
    coords = np.asarray(rect.exterior.coords)
    sides = np.hypot(*np.diff(coords[:3], axis=0).T)
    return min(sizes.max_m, float(sides.min()) / sizes.pad_divisions)


def _on_grid(geometry, what):
    """``geometry`` rounded onto the GRID_M grid, checked to be the same copper.

    The cleanup's unions can leave vertices a few nm apart, closer than Gmsh can tell
    apart; rounding to 10 nm (far below the 1 µm geometry rule) removes them."""
    rounded = set_precision(geometry, GRID_M)
    # Keep the rounded coordinates but not the attached grid: later operations (thin
    # copper detection) must work in ordinary floating point.
    floating = set_precision(rounded, 0)
    if not shapely.equals_exact(floating, rounded, tolerance=0):
        raise ValueError(f"{what}: coordinates moved when the grid was detached")
    rounded = floating
    if rounded.geom_type != geometry.geom_type or not rounded.is_valid or \
            abs(rounded.area - geometry.area) > geometry.length * GRID_M:
        # More than a grid step of copper along its whole outline: not the same copper.
        raise ValueError(f"{what}: changes when rounded to a {GRID_M * 1e9:.0f} nm grid "
                         f"({geometry.area:.6e} -> {rounded.area:.6e} m²)")
    return rounded


def _faces(piece, pads):
    """Split one piece along the outlines of the pads on it.

    Returns [(face polygon, pad index or -1)]. The piece's outline and the pad
    outlines are noded together once, so neighbouring faces share their edge points
    exactly and the mesh follows every pad outline (an equipotential pad contact needs
    whole triangles)."""
    g = piece.geometry
    mine = [(j, pad) for j, pad in pads if pad.geometry.intersection(g).area > 1e-12]
    if not mine:
        return [(g, -1)]
    # Fixed-precision noding: every point on the GRID_M grid, so faces share their edge
    # points exactly and no two points are closer than the grid.
    lines = union_all([g.boundary] + [pad.geometry.boundary for _, pad in mine],
                      grid_size=GRID_M)
    faces = []
    for face in polygonize(getattr(lines, "geoms", [lines])):
        inside = face.representative_point()
        if not g.contains(inside):
            continue                      # a hole, or outside the copper
        owner = next((j for j, pad in mine if pad.geometry.contains(inside)), -1)
        faces.append((face, owner))
    points = np.vstack([np.asarray(r.coords)[:-1] for f, _ in faces
                        for r in (f.exterior, *f.interiors)])
    unique = np.unique(points, axis=0)
    if len(unique) > 1:
        gap, _ = cKDTree(unique).query(unique, k=2)
        if gap[:, 1].min() < 0.5 * GRID_M:
            raise ValueError(f"{piece.id}: pad outlines leave points {gap[:, 1].min():.2e} m "
                             "apart after snapping")
    small = [f.area for f, _ in faces if f.area < AREA_MIN_M2]
    if small:
        raise ValueError(f"{piece.id}: splitting along pad outlines leaves a face of "
                         f"{min(small) * 1e12:.3g} µm²")
    covered = sum(f.area for f, _ in faces)
    if abs(covered - g.area) > 1e-9 * g.area:
        raise ValueError(f"{piece.id}: splitting along pad outlines changed its area "
                         f"({covered:.6e} against {g.area:.6e} m²)")
    return faces


def _write_geo(path, pieces, drills, pads, refine, sizes):
    """Write the .geo for one layer; return {physical name: (piece index, pad index)}
    and {rim name: drill id}."""
    lines = [f"// PDNLab board mesh, metres. scale={sizes.scale:.6g}",
             f"Geometry.Tolerance = {GRID_M * 1e-3:.3g};",
             f"Mesh.MeshSizeMax = {sizes.max_m * sizes.scale:.9g};",
             "Mesh.MeshSizeMin = 1e-7;",
             "Mesh.MeshSizeExtendFromBoundary = 0;",
             "Mesh.MeshSizeFromPoints = 1;",
             "Mesh.MeshSizeFromCurvature = 0;",
             "Mesh.Algorithm = 6;"]
    point_id, point_size, edges, body = {}, {}, {}, []
    faces_out, rules = {}, {}

    def point(xy, size):
        if xy not in point_id:
            point_id[xy] = len(point_id) + 1
            point_size[xy] = size
        else:
            point_size[xy] = min(point_size[xy], size)
        return point_id[xy]

    def edge(a, b):
        key = (min(a, b), max(a, b))
        if key not in edges:
            edges[key] = len(edges) + 1
        return edges[key] if a < b else -edges[key]

    surface = 0
    for k, piece in enumerate(pieces):
        regions = _thin_regions(piece.geometry, sizes)
        holes = {}
        for ring_index, drill_id in _hole_ring(piece.geometry, piece.drill_ids, drills).items():
            holes[drill_id] = piece.geometry.interiors[ring_index]
        rim_edges = {drill_id: set() for drill_id in holes}
        for face, owner in _faces(piece, pads):
            loops = []
            for ring in (face.exterior, *face.interiors):
                pts = _ring_points(ring)
                lc = np.minimum(_spacing(pts), sizes.max_m) * sizes.scale
                _cap_thin(pts, lc, regions, sizes)
                ids = [point((float(x), float(y)), float(s)) for (x, y), s in zip(pts, lc)]
                loop = [edge(ids[i], ids[(i + 1) % len(ids)]) for i in range(len(ids))]
                for drill_id, hole in holes.items():
                    for i in range(len(pts)):
                        a, b = pts[i], pts[(i + 1) % len(pts)]
                        mid = Point((a + b) / 2)
                        if hole.distance(Point(a)) < 1e-11 and hole.distance(Point(b)) < 1e-11 \
                                and hole.distance(mid) < 1e-9:
                            rim_edges[drill_id].add(abs(loop[i]))
                loops.append(loop)
            surface += 1
            body.append((surface, loops))
            faces_out[f"face_{surface}"] = (k, owner)
        for drill_id, ids in rim_edges.items():
            if not ids:
                raise ValueError(f"{drill_id}: its edge was not found in the faces of {piece.id}")
            side = float(np.mean(_spacing(_ring_points(holes[drill_id]))))
            rules.setdefault(round(side * 1e6), {"curves": [], "points": []})["curves"] += sorted(ids)
            body.append(("rim", drill_id, sorted(ids)))
    for xy, n in point_id.items():
        lines.append(f"Point({n}) = {{{xy[0]:.17g}, {xy[1]:.17g}, 0, {point_size[xy]:.9g}}};")
    for (a, b), n in edges.items():
        lines.append(f"Line({n}) = {{{a}, {b}}};")
    loop_id, rim_names = 0, {}
    for item in body:
        if item[0] == "rim":
            _, drill_id, ids = item
            name = f"rim_{len(rim_names)}"
            rim_names[name] = drill_id
            lines.append(f'Physical Curve("{name}") = {{{", ".join(map(str, ids))}}};')
            continue
        surface, loops = item
        refs = []
        for loop in loops:
            loop_id += 1
            lines.append(f"Curve Loop({loop_id}) = {{{', '.join(map(str, loop))}}};")
            refs.append(loop_id)
        lines.append(f"Plane Surface({surface}) = {{{', '.join(map(str, refs))}}};")
        lines.append(f'Physical Surface("face_{surface}") = {{{surface}}};')
    extra = len(point_id)
    for pad in refine:
        edge_size = _pad_edge(pad.geometry, sizes)
        ring = pad.geometry.exterior if pad.geometry.geom_type == "Polygon" else \
            max(pad.geometry.geoms, key=lambda g: g.area).exterior
        n = max(8, int(math.ceil(ring.length / edge_size)))
        rule = rules.setdefault(round(edge_size * 1e6), {"curves": [], "points": []})
        for i in range(n):
            x, y = ring.interpolate(i / n, normalized=True).coords[0]
            extra += 1
            lines.append(f"Point({extra}) = {{{x:.12g}, {y:.12g}, 0, {sizes.max_m * sizes.scale:.9g}}};")
            rule["points"].append(extra)
    fields = []
    for n, (edge_um, rule) in enumerate(sorted(rules.items())):
        dist, thr = 2 * n + 1, 2 * n + 2
        size = edge_um * 1e-6 * sizes.scale
        top = sizes.max_m * sizes.scale
        lines.append(f"Field[{dist}] = Distance;")
        if rule["curves"]:
            lines.append(f"Field[{dist}].CurvesList = {{{', '.join(map(str, rule['curves']))}}};")
            lines.append(f"Field[{dist}].Sampling = 20;")
        if rule["points"]:
            lines.append(f"Field[{dist}].PointsList = {{{', '.join(map(str, rule['points']))}}};")
        lines += [f"Field[{thr}] = Threshold;",
                  f"Field[{thr}].InField = {dist};",
                  f"Field[{thr}].SizeMin = {size:.9g};",
                  f"Field[{thr}].SizeMax = {top:.9g};",
                  f"Field[{thr}].DistMin = 0;",
                  f"Field[{thr}].DistMax = {max(top - size, 0) / (sizes.growth * sizes.scale):.9g};"]
        fields.append(thr)
    if fields:
        m = 2 * len(rules) + 1
        lines += [f"Field[{m}] = Min;", f"Field[{m}].FieldsList = {{{', '.join(map(str, fields))}}};",
                  f"Background Field = {m};"]
    Path(path).write_text("\n".join(lines) + "\n")
    return faces_out, rim_names


def _read(msh, faces, rims, layer_id, pieces, pads):
    mesh = meshio.read(msh)
    tags = mesh.cell_data_dict.get("gmsh:physical", {})
    tri_all, tri_tag = mesh.cells_dict.get("triangle"), tags.get("triangle")
    if tri_all is None or tri_tag is None:
        raise RuntimeError(f"{layer_id}: Gmsh produced no tagged triangles")
    by_tag = {int(tag): name for name, (tag, dim) in mesh.field_data.items()}
    owner = [faces.get(by_tag.get(int(t))) for t in tri_tag]
    keep = np.array([o is not None for o in owner])
    triangles = tri_all[keep]
    piece_of = np.array([o[0] for o in owner if o is not None], dtype=np.int64)
    pad_of = np.array([o[1] for o in owner if o is not None], dtype=np.int64)
    for k, piece in enumerate(pieces):
        if not np.any(piece_of == k):
            raise RuntimeError(f"{layer_id}: {piece.id} got no triangles")
    used = np.unique(triangles)
    mapping = np.full(len(mesh.points), -1, dtype=np.int64)
    mapping[used] = np.arange(len(used))
    hole_rims = {}
    line_all, line_tag = mesh.cells_dict.get("line"), tags.get("line")
    for name, drill_id in rims.items():
        tag = mesh.field_data[name][0]
        nodes = np.unique(line_all[line_tag == tag])
        if nodes.size == 0 or np.any(mapping[nodes] < 0):
            raise RuntimeError(f"{layer_id}: edge of {drill_id} not on the mesh")
        hole_rims[drill_id] = mapping[nodes]
    return (mesh.points[used, :2].copy(), mapping[triangles], piece_of, pad_of,
            tuple(p.id for p in pieces), tuple(p.id for _, p in pads), hole_rims)


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
            mine = [replace(p, geometry=_on_grid(p.geometry, p.id))
                    for p in pieces if p.layer_id == layer_id]
            geo, msh = folder / f"layer{n}.geo", folder / f"layer{n}.msh"
            pads = list(enumerate(replace(f, geometry=_on_grid(f.geometry, f.id))
                                  for f in board.features if f.layer_id == layer_id
                                  and f.net_id == net_id and f.kind == "pad"))
            refine = [f for _, f in pads if f.drill_id is None]
            faces, rims = _write_geo(geo, mine, drill_map, pads, refine, sizes)
            version = run_gmsh(geo, msh, executable=gmsh, timeout=timeout)
            nodes, triangles, piece_of, pad_of, piece_ids, pad_ids, hole_rims = _read(
                msh, faces, rims, layer_id, mine, pads)
            layers.append(LayerMesh(layer_id, nodes, triangles, piece_of, piece_ids, hole_rims,
                                    pad_of, pad_ids))
    finally:
        if context:
            context.cleanup()
    return NetMesh(net_id, tuple(layers), sizes, version, perf_counter() - start)

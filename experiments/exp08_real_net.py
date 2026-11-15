"""Prototype: DC drop of one net on a real KiCad 10 board. Not a product importer.

Reads the saved .kicad_pcb directly (not IPC-2581), builds copper polygons for one net on
each layer (tracks, pads, saved zone fill), meshes each layer with Gmsh as an external
program, assembles pdnlab_dc sheets, ties via rims and pins, joins layers with barrel
resistors, and solves.

Result: for each candidate supply entry S (pins held at 0 V drop) the transfer-resistance
matrix Z of the load pins. Z[p, k] is the voltage drop at pin p per ampere drawn at pin k,
so any load pattern is a matrix-vector product. No load currents are assumed by the solve.

Assumptions you must review (setup values, not measured): copper resistivity, via plating
thickness, copper thickness from the board stackup, pins as equipotential pads, connector
pin leads ideal across layers, saved zone fill is current.

Run: PYTHONPATH=src python experiments/exp08_real_net.py --board b.kicad_pcb --net +3V3 \
       --source J4=J4.6,J4.10,J4.13,J4.16 --source J1=J1.1 --out /tmp/exp08
"""

import argparse
import json
import math
from pathlib import Path

import meshio
import numpy as np
import scipy.sparse as sp
import shapely
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu
from scipy.spatial import cKDTree
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.validation import make_valid

from pdnlab_dc import fem
from pdnlab_dc.gmsh_mesh import run_gmsh
from pdnlab_dc.ipc2581_inspect import _children, _net_namer, _one, parse_sexpr
from pdnlab_dc.vias import barrel_resistance

MM = 1e-3
CIRCLE_SEGMENTS = 24  # hole polygons; their vertices lie on the circle, edges just inside


# ---------------------------------------------------------------- board reading
def _rot(x, y, degrees):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return x * c + y * s, -x * s + y * c  # KiCad: counter-clockwise on screen, Y down


def _angle(node):
    return float(node[3]) if len(node) > 3 else 0.0


def _pad_polygon(pad, centre, angle):
    size = _one(pad, "size")
    w, h = float(size[1]), float(size[2])
    shape = pad[3]
    if shape == "circle":
        poly = Point(centre).buffer(w / 2, 32)
    elif shape == "rect":
        poly = box(centre[0] - w / 2, centre[1] - h / 2, centre[0] + w / 2, centre[1] + h / 2)
    elif shape == "roundrect":
        ratio = float(_one(pad, "roundrect_rratio")[1])
        r = ratio * min(w, h)
        poly = box(centre[0] - w / 2 + r, centre[1] - h / 2 + r,
                   centre[0] + w / 2 - r, centre[1] + h / 2 - r).buffer(r, 8)
    else:
        raise ValueError(f"pad shape {shape!r} is not supported")
    return affinity.rotate(poly, -angle, origin=centre)


def read_net(path, net):
    root = parse_sexpr(Path(path).read_text(encoding="utf-8"))
    name = _net_namer(root)
    copper = [i[1] for i in _one(root, "layers")[1:]
              if isinstance(i, list) and len(i) > 2 and i[2] in {"signal", "power", "mixed", "jumper"}]
    # Layer centres along the stack, from the first copper layer's top face.
    z, centres, started = 0.0, {}, False
    for layer in _children(_one(_one(root, "setup"), "stackup"), "layer"):
        thickness = _children(layer, "thickness")
        is_cu = layer[1].endswith(".Cu")
        if not thickness or not (is_cu or layer[1].startswith("dielectric")) or (not started and not is_cu):
            continue
        started, t = True, float(thickness[0][1])
        if is_cu:
            centres[layer[1]] = (z + t / 2, t)
        z += t
    tracks = []
    for s in _children(root, "segment"):
        if name(_one(s, "net")[1]) == net:
            a, b = _one(s, "start"), _one(s, "end")
            tracks.append((_one(s, "layer")[1], (float(a[1]), float(a[2])), (float(b[1]), float(b[2])),
                           float(_one(s, "width")[1])))
    vias = [dict(c=(float(_one(v, "at")[1]), float(_one(v, "at")[2])), size=float(_one(v, "size")[1]),
                 drill=float(_one(v, "drill")[1]), layers=_one(v, "layers")[1:])
            for v in _children(root, "via") if name(_one(v, "net")[1]) == net]
    pads = []
    for fp in _children(root, "footprint"):
        at = _one(fp, "at")
        ref = [p[2] for p in _children(fp, "property") if p[1] == "Reference"][0]
        for pad in _children(fp, "pad"):
            n = _children(pad, "net")
            if not n or name(n[0][1]) != net:
                continue
            pat = _one(pad, "at")
            lx, ly = _rot(float(pat[1]), float(pat[2]), _angle(at))
            centre = (float(at[1]) + lx, float(at[2]) + ly)
            layers = _one(pad, "layers")[1:]
            on = copper if "*.Cu" in layers else [l for l in layers if l in copper]
            drill = _children(pad, "drill")
            if drill and drill[0][1] == "oval":
                raise ValueError("oval pad holes are not supported")
            pads.append(dict(ref=ref, pin=pad[1], kind=pad[2], layers=on, centre=centre,
                             poly=_pad_polygon(pad, centre, _angle(pat)),
                             drill=float(drill[0][1]) if drill else None,
                             type=[x[1] for x in _children(pad, "pintype")][0] if _children(pad, "pintype") else ""))
    zones, relief = {}, {}
    for z_ in _children(root, "zone"):
        if name(_one(z_, "net")[1]) != net:
            continue
        gap = [float(g[1]) for f in _children(z_, "fill") for g in _children(f, "thermal_gap")]
        for f in _children(z_, "filled_polygon"):
            pts = [(float(p[1]), float(p[2])) for p in _children(_one(f, "pts"), "xy")]
            zones.setdefault(_one(f, "layer")[1], []).append(make_valid(Polygon(pts)))
            # Largest thermal-relief gap of this net's zones on the layer; 0 if the file gives none.
            relief[_one(f, "layer")[1]] = max(relief.get(_one(f, "layer")[1], 0.0), *gap, 0.0)
    return dict(copper=copper, centres=centres, tracks=tracks, vias=vias, pads=pads, zones=zones, relief=relief)


# ------------------------------------------------------------- layer geometry
def _polygons(geometry, min_area=1e-6):
    out = []
    for g in getattr(geometry, "geoms", [geometry]):
        if g.geom_type == "Polygon" and g.area > min_area:
            out.append(g)
        elif g.geom_type in {"MultiPolygon", "GeometryCollection"}:
            out.extend(_polygons(g, min_area))
    return out


def build_layers(board, plating_mm, rim_segments=None):
    """Per-layer copper polygons (mm) with via/TH holes cut, plus the via/pad contact map.

    rim_segments=None keeps the original geometry (24-sided holes, outline simplified after
    the cut). An integer sets the number of sides of each via hole; the outline is then
    simplified before the cut, because the 2 um simplification would otherwise remove
    vertices of a finely divided hole.
    """
    if rim_segments is not None and (rim_segments != int(rim_segments) or rim_segments < 16 or rim_segments % 4):
        raise ValueError("rim_segments must be a multiple of 4, at least 16")
    layers = {}
    outer = {board["copper"][0], board["copper"][-1]}
    for layer in board["copper"]:
        base = [LineString([a, b]).buffer(w / 2, 8) if a != b else Point(a).buffer(w / 2, 8)
                for (l, a, b, w) in board["tracks"] if l == layer]
        base += [p for z in board["zones"].get(layer, []) for p in _polygons(z)]
        base += [p["poly"] for p in board["pads"] if p["kind"] == "smd" and layer in p["layers"]]
        other = unary_union(base) if base else Polygon()
        thru = [p for p in board["pads"] if p["kind"] == "thru_hole" and layer in p["layers"]
                and (layer in outer or p["poly"].intersection(other).area > 1e-6)]
        merged = unary_union([other] + [p["poly"] for p in thru])
        contact = [i for i, v in enumerate(board["vias"])
                   if Point(v["c"]).buffer(v["size"] / 2, 16).intersection(merged).area > 1e-6]
        copper = unary_union([merged] + [Point(board["vias"][i]["c"]).buffer(board["vias"][i]["size"] / 2, 16)
                                         for i in contact])
        holes = [(board["vias"][i]["c"], board["vias"][i]["drill"] / 2 + plating_mm, ("via", i)) for i in contact]
        holes += [(p["centre"], p["drill"] / 2 + plating_mm, ("pad", p["ref"] + "." + p["pin"])) for p in thru]
        if rim_segments is None:
            for c, r, _ in holes:
                copper = copper.difference(Point(c).buffer(r, CIRCLE_SEGMENTS // 4))
            polys = _polygons(copper.simplify(0.002))
        else:
            copper = copper.simplify(0.002)
            for c, r, tag in holes:
                sides = rim_segments if tag[0] == "via" else CIRCLE_SEGMENTS
                copper = copper.difference(Point(c).buffer(r, sides // 4))
            polys = _polygons(copper)
        # Through-hole pads that feed copper beyond their own outline (a pad that is a
        # separate island is one equipotential and needs no refinement).
        fed = []
        for p in thru:
            host = [q for q in polys if q.intersects(p["poly"])]
            if sum(q.area - q.intersection(p["poly"]).area for q in host) > 1e-3:
                reach = max(math.hypot(x - p["centre"][0], y - p["centre"][1]) for x, y in p["poly"].exterior.coords)
                fed.append(dict(pin=p["ref"] + "." + p["pin"], centre=p["centre"], reach=reach,
                                hole_radius=p["drill"] / 2 + plating_mm))
        layers[layer] = dict(polys=polys, holes=holes, via_contacts=contact,
                             rim_segments=CIRCLE_SEGMENTS if rim_segments is None else int(rim_segments),
                             fed_pads=fed, relief_mm=board.get("relief", {}).get(layer, 0.0))
    return layers


# ------------------------------------------------------------------- meshing
def write_geo(path, polys, points, hmin, hmax, refine=()):
    """Polygons with holes to a .geo (mm); points are embedded into the surface containing them.

    refine: (cx, cy, radius, edge_size, ramp) per circular hole. Adds a Gmsh size field that
    is edge_size at the hole edge and grows linearly to hmax over the distance ramp. It acts
    together with the point sizes: Gmsh uses the smaller of the two everywhere.
    """
    lines, pid, lid, cid = [], 0, 0, 0
    loops = []

    def ring(coords):
        nonlocal pid, lid, cid
        c = np.asarray(coords)[:-1]
        n = len(c)
        seg = np.hypot(*(c - np.roll(c, -1, axis=0)).T)
        lc = np.clip(0.5 * (seg + np.roll(seg, 1)), hmin, hmax)
        first = pid + 1
        for (x, y), l in zip(c, lc):
            pid += 1
            lines.append(f"Point({pid}) = {{{x:.9g}, {y:.9g}, 0, {l:.6g}}};")
        ids = []
        for i in range(n):
            lid += 1
            lines.append(f"Line({lid}) = {{{first + i}, {first + (i + 1) % n}}};")
            ids.append(lid)
        cid += 1
        lines.append(f"Curve Loop({cid}) = {{{', '.join(map(str, ids))}}};")
        return cid

    surfaces = []
    for k, poly in enumerate(polys, start=1):
        outer = ring(poly.exterior.coords)
        holes = [ring(r.coords) for r in poly.interiors]
        lines.append(f"Plane Surface({k}) = {{{', '.join(map(str, [outer] + holes))}}};")
        surfaces.append(k)
    for (x, y), k in points:
        pid += 1
        lines.append(f"Point({pid}) = {{{x:.9g}, {y:.9g}, 0, {hmin:.6g}}};")
        lines.append(f"Point{{{pid}}} In Surface{{{k}}};")
    size_min = hmin / 2
    if refine:
        classes = {}
        for cx, cy, r, edge, ramp in refine:
            if not (0 < edge < hmax and ramp > 0 and r > 0):
                raise ValueError("refinement needs 0 < edge_size < hmax, ramp > 0 and radius > 0")
            pid += 1  # centre point: used only by the Distance field, it lies in the hole
            lines.append(f"Point({pid}) = {{{cx:.9g}, {cy:.9g}, 0, {hmax:.6g}}};")
            classes.setdefault((round(r, 9), round(edge, 9), round(ramp, 9)), []).append(pid)
        thresholds = []
        for i, ((r, edge, ramp), ids) in enumerate(sorted(classes.items())):
            d, t = 2 * i + 1, 2 * i + 2
            lines += [f"Field[{d}] = Distance;", f"Field[{d}].PointsList = {{{', '.join(map(str, ids))}}};",
                      f"Field[{t}] = Threshold;", f"Field[{t}].InField = {d};",
                      f"Field[{t}].SizeMin = {edge:.9g};", f"Field[{t}].SizeMax = {hmax:.9g};",
                      f"Field[{t}].DistMin = {r:.9g};", f"Field[{t}].DistMax = {r + ramp:.9g};"]
            thresholds.append(t)
            size_min = min(size_min, edge / 2)
        m = 2 * len(classes) + 1
        lines += [f"Field[{m}] = Min;", f"Field[{m}].FieldsList = {{{', '.join(map(str, thresholds))}}};",
                  f"Background Field = {m};"]
    lines += [f'Physical Surface("copper", 1) = {{{", ".join(map(str, surfaces))}}};',
              f"Mesh.MeshSizeMax = {hmax};", f"Mesh.MeshSizeMin = {size_min:.9g};",
              "Mesh.MeshSizeFromPoints = 1;", "Mesh.MeshSizeExtendFromBoundary = 1;",
              "Mesh.Algorithm = 6;", "Mesh.ElementOrder = 1;", "Mesh.MshFileVersion = 4.1;", "Mesh.Binary = 0;"]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def mesh_layer(name, layer, pads, workdir, h, gmsh, growth=None):
    """Mesh one layer. growth=None keeps the original sizing (point sizes only).

    A number adds local refinement, growing by `growth` mm per mm of distance up to h:
    - at every via hole, from an element size equal to one side of the hole polygon;
    - at every through-hole pad that feeds copper on this layer, with the same rule applied
      to the pad's own hole (2 pi r / sides). The fine size is held over the pad and over
      1.25 x the zone's thermal-relief gap beyond it, so that the relief spokes and the
      corners where they join the plane are resolved.
    """
    polys = layer["polys"]
    refine, suffix = [], ""
    if growth is not None:
        if not 0 < growth <= 1:
            raise ValueError("growth must be in (0, 1]")
        for c, r, tag in layer["holes"]:
            if tag[0] == "via":
                edge = 2 * math.pi * r / layer["rim_segments"]
                if edge >= h:
                    raise ValueError(f"via hole side {edge:.3g} mm is not below h = {h:g} mm; nothing to refine")
                refine.append((c[0], c[1], r, edge, (h - edge) / growth))
        for p in layer["fed_pads"]:
            edge = 2 * math.pi * p["hole_radius"] / layer["rim_segments"]
            if edge >= h:
                raise ValueError(f"pad {p['pin']}: refinement size {edge:.3g} mm is not below h = {h:g} mm")
            refine.append((p["centre"][0], p["centre"][1], p["reach"] + 1.25 * layer["relief_mm"], edge,
                           (h - edge) / growth))
        suffix = f"_n{layer['rim_segments']}_g{growth:g}"
    points = []
    for p in pads:
        if name not in p["layers"]:
            continue
        cover = p["poly"].intersection(unary_union(polys))
        if cover.is_empty or cover.area < 1e-6:
            continue
        q = Point(p["centre"]) if cover.contains(Point(p["centre"])) else cover.representative_point()
        k = next(i for i, poly in enumerate(polys, start=1) if poly.contains(q))
        points.append(((q.x, q.y), k))
        # A pad inside a larger copper region has no mesh edge along its outline, so the pad
        # equipotential would only be placed to within the node spacing. Force nodes onto the
        # outline instead (skipping stretches that already lie on the copper boundary).
        edge = unary_union(polys).boundary
        for ring in getattr(cover.boundary, "geoms", [cover.boundary]):
            for d in np.arange(0.0, ring.length, h / 8):
                q = ring.interpolate(d)
                if edge.distance(q) > h / 32:
                    points.append(((q.x, q.y), next(i for i, poly in enumerate(polys, start=1) if poly.contains(q))))
    # Plain string concatenation: Path.with_suffix would treat the decimals of h as a suffix.
    stem = str(Path(workdir) / f"{name.replace('.', '_')}_h{h:g}{suffix}")
    geo, msh = Path(stem + ".geo"), Path(stem + ".msh")
    write_geo(geo, polys, points, h / 8, h, refine)
    version = run_gmsh(geo, msh, executable=gmsh, timeout=600)
    m = meshio.read(msh)
    tri = m.cells_dict["triangle"]
    used = np.unique(tri)
    remap = np.full(len(m.points), -1)
    remap[used] = np.arange(len(used))
    return m.points[used, :2], remap[tri], version


# ------------------------------------------------------------------- assembly
class _Union:
    def __init__(self, n):
        self.p = np.arange(n)

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def join(self, nodes):
        r = self.find(nodes[0])
        for n in nodes[1:]:
            self.p[self.find(n)] = r


def _check_rim_loop(tri, ids, vi, name):
    """A via rim must be one closed loop of mesh-boundary edges; anything else is a wrong node set."""
    edges = np.sort(np.vstack([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]), axis=1)
    edges = edges[np.isin(edges, ids).all(axis=1)]
    uniq, count = np.unique(edges, axis=0, return_counts=True)
    boundary = uniq[count == 1]
    degree = np.bincount(np.searchsorted(ids, boundary.ravel()), minlength=len(ids))
    if len(boundary) != len(ids) or np.any(degree != 2):
        raise RuntimeError(f"via {vi} on {name}: the {len(ids)} rim nodes do not form one closed hole boundary")


def assemble(board, layers, meshes, rho, plating_mm):
    offsets, nodes_mm, blocks, thick = {}, [], [], {}
    total = 0
    for name in meshes:
        pts, tri, _ = meshes[name]
        offsets[name] = total
        total += len(pts)
        nodes_mm.append(pts)
        t = board["centres"][name][1] * MM
        thick[name] = t
        blocks.append(fem.assemble_sheet(pts * MM, tri, t / rho))
    G = sp.block_diag(blocks, format="csr")
    xy = np.vstack(nodes_mm)
    layer_of = np.concatenate([np.full(len(meshes[n][0]), i) for i, n in enumerate(meshes)])
    uf = _Union(total)

    # Pin terminals: every mesh node inside the pad copper; pins tie across layers (ideal lead).
    pin_nodes = {}
    for p in board["pads"]:
        key = p["ref"] + "." + p["pin"]
        ids = []
        for name in p["layers"]:
            if name not in meshes:
                continue
            pts = meshes[name][0]
            inside = shapely.contains_xy(p["poly"].buffer(1e-4), pts[:, 0], pts[:, 1])
            ids.extend((np.flatnonzero(inside) + offsets[name]).tolist())
        pin_nodes[key] = np.array(sorted(set(ids)), dtype=int)
        if pin_nodes[key].size:
            uf.join(pin_nodes[key].tolist())

    # Via rims: one rim node per contacted layer, tied on that layer only.
    rims, rim_nodes, segments = {}, {}, []
    for vi, v in enumerate(board["vias"]):
        rim_r = v["drill"] / 2 + plating_mm
        got = []
        for name in meshes:
            if vi not in layers[name]["via_contacts"]:
                continue
            pts = meshes[name][0]
            d = np.hypot(pts[:, 0] - v["c"][0], pts[:, 1] - v["c"][1])
            ids = np.flatnonzero((d >= 0.985 * rim_r) & (d <= 1.001 * rim_r)) + offsets[name]
            if ids.size < 3:
                raise RuntimeError(f"via {vi} has {ids.size} rim nodes on {name}")
            _check_rim_loop(meshes[name][1], ids - offsets[name], vi, name)
            uf.join(ids.tolist())
            got.append((name, int(ids[0])))
            rim_nodes[(vi, name)] = ids
        rims[vi] = got
    root_of = np.array([uf.find(i) for i in range(total)])
    groups = {}
    for i, r in enumerate(root_of):
        groups.setdefault(r, []).append(i)
    groups = [np.array(g) for g in groups.values() if len(g) > 1]
    reduced, node_map = fem.tie_nodes(G, groups)

    pairs, res = [], []
    for vi, got in rims.items():
        v = board["vias"][vi]
        for (la, na), (lb, nb) in zip(got, got[1:]):
            a, b = node_map[na], node_map[nb]
            if a == b:
                raise RuntimeError(f"via {vi}: rims on {la} and {lb} merged; barrel resistance would vanish")
            length = abs(board["centres"][lb][0] - board["centres"][la][0]) * MM
            pairs.append((a, b))
            res.append(barrel_resistance(length, v["drill"] / 2 * MM, (v["drill"] / 2 + plating_mm) * MM, rho))
            segments.append(dict(via=vi, upper=la, lower=lb, a=int(a), b=int(b), ohm=res[-1]))
    if pairs:
        reduced = fem.add_resistors(reduced, np.array(pairs), np.array(res))
    pin_red = {k: np.unique(node_map[v]) for k, v in pin_nodes.items() if v.size}
    return dict(G=reduced, node_map=node_map, xy=xy, layer_of=layer_of, offsets=offsets, pin_red=pin_red,
                missing_pins=[k for k, v in pin_nodes.items() if not v.size], barrel_r=np.array(res),
                n_vias_modelled=sum(1 for g in rims.values() if len(g) > 1),
                # For per-via post-processing: unreduced sheet matrix, rim node sets, barrel segments.
                G_sheets=G, names=list(meshes), thick=thick, rim_nodes=rim_nodes, segments=segments,
                pin_nodes={k: v for k, v in pin_nodes.items() if v.size}, plating_mm=plating_mm)


# --------------------------------------------------------------------- solve
def transfer_matrix(model, source_pins, load_pins):
    """Z[p, k]: drop at load pin p per ampere drawn at load pin k, source held at 0 drop."""
    G = model["G"]
    count, labels = connected_components(G, directed=False)
    src = np.unique(np.concatenate([model["pin_red"][s] for s in source_pins if s in model["pin_red"]]))
    reach = labels == labels[src[0]]
    if not np.all(labels[src] == labels[src[0]]):
        raise RuntimeError("source pins are in different copper islands")
    fixed = src
    free = np.flatnonzero(reach & ~np.isin(np.arange(G.shape[0]), fixed))
    lu = splu(G[free][:, free].tocsc())
    pos = {n: i for i, n in enumerate(free)}
    loads, unreachable = [], []
    for k in load_pins:
        nodes = model["pin_red"].get(k)
        if nodes is None or not reach[nodes[0]]:
            unreachable.append(k)
        elif nodes[0] in fixed:
            continue
        else:
            loads.append(k)
    rhs = np.zeros((len(free), len(loads)))
    for j, k in enumerate(loads):
        rhs[pos[model["pin_red"][k][0]], j] = 1.0  # 1 A drawn at pin k
    sol = lu.solve(rhs)
    Z = np.array([[sol[pos[model["pin_red"][p][0]], j] for j in range(len(loads))] for p in loads])
    return loads, Z, unreachable, dict(free=free, lu=lu, fixed=fixed, reach=reach)


def solve_scenario(model, ctx, currents):
    """Voltage drop at every reduced node for given load currents {pin: amperes}, with checks."""
    G = model["G"]
    n = G.shape[0]
    inject = np.zeros(n)
    for k, i in currents.items():
        inject[model["pin_red"][k][0]] -= i
    v = fem.solve_fixed_voltages(G, ctx["fixed"], 0.0, injected_current=inject)
    supplied = fem.node_currents(G, v)[ctx["fixed"]].sum()  # reaction at the source: current it feeds in
    total_load = sum(currents.values())
    # Drawing current lowers voltage, so return the drop (positive) rather than the voltage change.
    return -v, dict(source_current_A=float(supplied), load_current_A=float(total_load),
                    imbalance_A=float(abs(supplied - total_load)))


# ------------------------------------------------------------- per-via output
def via_currents(board, model, drop):
    """Per-via currents and current densities for one solved scenario.

    drop: voltage drop at every reduced node, as returned by solve_scenario.

    Two evaluations that do not share data are returned so they can be checked against each
    other: the barrel current comes from the lumped barrel resistor, (v_upper - v_lower) / R;
    the rim current comes from the layer's own sheet matrix, summed over the nodes on the hole.

    Signs: a barrel current is positive when it flows from the upper layer to the lower one
    (stack order of the board file). A rim current is positive when it leaves that layer's
    copper and enters the via.

    Densities, A/mm2: j_barrel is the largest barrel current over the plating cross-section
    pi * (r_o**2 - r_i**2). j_edge_avg is the rim current over the hole perimeter times the
    layer thickness. j_edge_peak is the largest local value round the hole, from the nodal
    currents of the rim nodes; it stays above zero for a via that carries no net current,
    because an equipotential rim lets plane current enter on one side and leave on the other.
    """
    u_red = -np.asarray(drop, dtype=float)
    u = u_red[model["node_map"]]
    q = model["G_sheets"] @ u  # current each sheet node receives from outside its own sheet
    pin_of = {int(n): k for k, nodes in model["pin_red"].items() for n in nodes}
    order_of = {n: i for i, n in enumerate(model["names"])}
    out = []
    for vi, v in enumerate(board["vias"]):
        contacts = [n for n in model["names"] if (vi, n) in model["rim_nodes"]]
        r_i, r_o = v["drill"] / 2, v["drill"] / 2 + model["plating_mm"]
        area = math.pi * (r_o - r_i) * (r_o + r_i)
        rim, edge_avg, edge_peak = {}, {}, {}
        for name in contacts:
            ids = model["rim_nodes"][(vi, name)]
            if int(model["node_map"][ids[0]]) in pin_of:
                raise NotImplementedError(
                    f"via {vi}: its rim on {name} is tied to pin {pin_of[int(model['node_map'][ids[0]])]} "
                    "(via in pad); rim and pad current cannot be separated in this model")
            xy = model["xy"][ids]
            turn = np.argsort(np.arctan2(xy[:, 1] - v["c"][1], xy[:, 0] - v["c"][0]))
            pts, qi = xy[turn], q[ids][turn]
            side = np.hypot(*(np.roll(pts, -1, axis=0) - pts).T)  # mm, node i to node i+1
            t = model["thick"][name] / MM  # mm
            rim[name] = float(-qi.sum())
            edge_avg[name] = float(abs(qi.sum()) / (side.sum() * t))
            edge_peak[name] = float(np.max(np.abs(qi) / (0.5 * (side + np.roll(side, 1)) * t)))
        barrel = [dict(upper=s["upper"], lower=s["lower"], ohm=s["ohm"],
                       amp=float((u_red[s["a"]] - u_red[s["b"]]) / s["ohm"]))
                  for s in model["segments"] if s["via"] == vi]
        # Kirchhoff at each rim: what the layer feeds in, plus what arrives from above,
        # must equal what leaves downward.
        residual = {}
        for name in contacts:
            above = sum(b["amp"] for b in barrel if b["lower"] == name)
            below = sum(b["amp"] for b in barrel if b["upper"] == name)
            residual[name] = rim[name] + above - below
        peak = max((abs(b["amp"]) for b in barrel), default=0.0)
        out.append(dict(via=vi, x=v["c"][0], y=v["c"][1], drill=v["drill"], size=v["size"],
                        layers=sorted(contacts, key=order_of.get), barrel=barrel, rim=rim,
                        barrel_abs_max=peak, plating_area_mm2=area, j_barrel=peak / area,
                        j_edge_avg=edge_avg, j_edge_peak=edge_peak,
                        rim_sum=float(sum(rim.values())),
                        kcl_residual=float(max((abs(x) for x in residual.values()), default=0.0))))
    return out


def via_conservation(model, ctx, drop, vias, atol=1e-9, rtol=1e-6):
    """Conservation gates for the per-via output. Returns a report with an overall "ok".

    1. Per via: at every rim, layer current in + barrel current from above = barrel current
       leaving downward; and the rim currents of one via sum to zero.
    2. Per terminal (pin): the current its sheet nodes exchange, summed over layers, equals
       the external current the solver reports at that pin (source reaction, minus the load).
    3. Per gap between neighbouring layers: the sum of all barrel currents crossing the gap
       equals the net terminal current the solver reports for the layers above it.
       A pin tied across layers (ideal through-hole lead) is not a barrel: the current it
       passes between layers is taken from its sheet nodes and listed under "ideal_leads".

    Tolerance: atol + rtol * source current (1 nA + 1e-6, the release 0.1 direct-solve gates).
    """
    u_red = -np.asarray(drop, dtype=float)
    q = model["G_sheets"] @ u_red[model["node_map"]]
    external = fem.node_currents(model["G"], u_red)  # into each reduced node, from outside
    source = float(external[ctx["fixed"]].sum())
    tol = atol + rtol * abs(source)
    names = model["names"]

    terminals, pin_error = [], 0.0
    for node in sorted({int(n) for nodes in model["pin_red"].values() for n in nodes}):
        members = np.flatnonzero(model["node_map"] == node)
        per_layer = {names[l]: float(q[members[model["layer_of"][members] == l]].sum())
                     for l in np.unique(model["layer_of"][members])}
        pins = sorted(k for k, nodes in model["pin_red"].items() if node in nodes)
        pin_error = max(pin_error, abs(sum(per_layer.values()) - external[node]))
        terminals.append(dict(pins=pins, external=float(external[node]), per_layer=per_layer))

    gaps = []
    for g in range(len(names) - 1):
        upper = set(names[:g + 1])
        barrels = sum(b["amp"] for v in vias for b in v["barrel"]
                      if names.index(b["upper"]) <= g < names.index(b["lower"]))
        needed = 0.0
        for t in terminals:
            if len(t["per_layer"]) == 1:
                needed += t["external"] if next(iter(t["per_layer"])) in upper else 0.0
            else:
                needed += sum(a for l, a in t["per_layer"].items() if l in upper)
        gross = sum(abs(b["amp"]) for v in vias for b in v["barrel"]
                    if names.index(b["upper"]) <= g < names.index(b["lower"]))
        gaps.append(dict(gap=f"{names[g]}|{names[g + 1]}", barrels_down_A=float(barrels), barrels_abs_sum_A=float(gross),
                         solver_terminals_above_A=float(needed), difference_A=float(barrels - needed)))

    kcl = max((v["kcl_residual"] for v in vias), default=0.0)
    rim_sum = max((abs(v["rim_sum"]) for v in vias), default=0.0)
    gap_error = max((abs(g["difference_A"]) for g in gaps), default=0.0)
    leads = [t for t in terminals if len(t["per_layer"]) > 1]
    return dict(ok=bool(max(kcl, rim_sum, pin_error, gap_error) <= tol), tolerance_A=tol, source_current_A=source,
                max_rim_kcl_residual_A=float(kcl), max_via_rim_sum_A=float(rim_sum),
                max_pin_sheet_vs_solver_A=float(pin_error), max_gap_difference_A=float(gap_error),
                gaps=gaps, ideal_leads=leads, vias_checked=len(vias),
                vias_with_barrel=sum(1 for v in vias if v["barrel"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", type=Path, required=True)
    ap.add_argument("--net", default="+3V3")
    ap.add_argument("--source", action="append", required=True, help="NAME=REF.PIN,REF.PIN")
    ap.add_argument("--extra-load-ref", action="append", default=[], help="also treat all pins of REF as loads")
    ap.add_argument("--h", type=float, default=0.4, help="coarse mesh size in mm; a second run uses h/2")
    ap.add_argument("--rho", type=float, default=1.724e-8, help="copper resistivity, ohm*m at 20 C")
    ap.add_argument("--plating-um", type=float, default=25.0)
    ap.add_argument("--gmsh", default="gmsh")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    board = read_net(a.board, a.net)
    plating = a.plating_um * 1e-3
    layers = build_layers(board, plating)
    sources = {s.split("=")[0]: s.split("=")[1].split(",") for s in a.source}
    loads = [p["ref"] + "." + p["pin"] for p in board["pads"]
             if p["type"] == "power_in" or p["ref"] in a.extra_load_ref]
    report = dict(board=str(a.board), net=a.net, rho_ohm_m=a.rho, plating_um=a.plating_um,
                  copper_um={k: v[1] * 1e3 for k, v in board["centres"].items()},
                  layers={n: dict(polygons=len(l["polys"]), area_mm2=round(sum(p.area for p in l["polys"]), 2),
                                  vias_in_contact=len(l["via_contacts"])) for n, l in layers.items()},
                  pins=len(board["pads"]), vias=len(board["vias"]), loads=loads, runs={})
    for h in (a.h, a.h / 2):
        meshes = {n: mesh_layer(n, layers[n], board["pads"], a.out, h, a.gmsh)
                  for n in board["copper"] if layers[n]["polys"]}
        model = assemble(board, layers, meshes, a.rho, plating)
        run = dict(nodes={n: len(m[0]) for n, m in meshes.items()}, triangles={n: len(m[1]) for n, m in meshes.items()},
                   gmsh=next(iter(meshes.values()))[2], reduced_nodes=model["G"].shape[0],
                   vias_with_barrel=model["n_vias_modelled"], pins_without_nodes=model["missing_pins"],
                   barrel_mohm=[float(model["barrel_r"].min() * 1e3), float(model["barrel_r"].max() * 1e3)]
                   if model["barrel_r"].size else None, sources={})
        for sname, spins in sources.items():
            ids, Z, unreachable, ctx = transfer_matrix(model, spins, loads)
            run["sources"][sname] = dict(loads=ids, Z_mohm=(Z * 1e3).tolist(), unreachable=unreachable)
        run["_model"] = model
        report["runs"][f"h={h:g}mm"] = run
    keys = list(report["runs"])
    gate = {}
    for sname in sources:
        c, f = (report["runs"][k]["sources"][sname] for k in keys)
        common = [p for p in c["loads"] if p in f["loads"]]
        dc = {p: np.array(c["Z_mohm"])[c["loads"].index(p)][c["loads"].index(p)] for p in common}
        df = {p: np.array(f["Z_mohm"])[f["loads"].index(p)][f["loads"].index(p)] for p in common}
        # Plan gate 5 on a 100 mA draw: |change| <= 10 uV + 1 % of the drop.
        gate[sname] = {p: dict(coarse_mohm=dc[p], fine_mohm=df[p],
                               ok=bool(abs(dc[p] - df[p]) * 0.1 <= 0.01 + 0.01 * abs(df[p]) * 0.1)) for p in common}
    report["mesh_convergence_self_impedance"] = gate
    for run in report["runs"].values():
        run.pop("_model")
    (a.out / "results.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("layers", "pins", "vias")}, indent=1))
    print("results:", a.out / "results.json")


if __name__ == "__main__":
    main()

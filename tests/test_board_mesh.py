"""Meshing one net of a cleaned board (board_mesh.mesh_net), checked by measurement.

Accuracy, measured 2026-11-22 (docs/DECISIONS.md):
* A copper ring (0.3 mm via plus 25 µm plating to a 5 mm radius edge) against the
  exact ln(b/a) / (2 pi sigma t): within 0.15 % at scale 1; refining settles about
  0.1 % high, the 1 µm hole rule (vertices on the circle make the hole slightly
  smaller), so it leans to a slightly higher drop, never lower.
* Two paths on the thermal board's GND net, against their converged values,
  extrapolated (Richardson) from scales 1 to 0.125 (up to 1.7 million triangles per
  layer): (A) In1.Cu, TP1's hole edge through four 0.5 mm spokes and the plane to a
  via's hole edge, 0.8579 mOhm (halvings changed it 0.42 %, 0.17 %, 0.07 %); (B) F.Cu,
  R1.2's SMD pad as an exact equipotential contact through three 0.3 mm spokes to the
  same via, 0.9069 mOhm (0.45 % then 0.21 % at the last halvings: current crowds at the
  pad edge). Default sizes must be within release gate 5's 1 % at scale 1.
Needs a gmsh executable (skipped without one, like the other Gmsh tests).
"""

import math
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from pdnlab_dc.board import CopperFeature, DrillFeature
from pdnlab_dc.board_mesh import GRID_M, MeshSizes, mesh_net
from pdnlab_dc.contacts import select_area_contact
from pdnlab_dc.copper_cleanup import clean_copper
from pdnlab_dc.fem import assemble_sheet, resistance_between
from pdnlab_dc.ipc2581_board import read_ipc2581_board

pytestmark = pytest.mark.skipif(shutil.which("gmsh") is None, reason="external Gmsh not installed")

FIXTURE = Path(__file__).parent / "fixtures" / "thermal_relief" / "thermal_relief.xml"
PLATING = 25e-6
SHEET = 5.8e7 * 35e-6            # siemens; only ratios matter here
CONVERGED = {"A": 0.8579e-3, "B": 0.9069e-3}


@pytest.fixture(scope="module")
def thermal():
    board = read_ipc2581_board(FIXTURE)
    return board, clean_copper(board, PLATING)


@pytest.fixture(scope="module")
def meshes(thermal):
    board, clean = thermal
    return {s: mesh_net(clean, board, "GND", MeshSizes(scale=s)) for s in (1.0, 0.5)}


def layer(mesh, layer_id):
    return next(L for L in mesh.layers if L.layer_id == layer_id)


def triangle_sizes(L):
    p = L.nodes[L.triangles]
    d1, d2 = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
    area = 0.5 * np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])
    return area, np.sqrt(area * 4 / np.sqrt(3)), p.mean(axis=1)


def paths(board, mesh):
    a = layer(mesh, "In1.Cu")
    ra = resistance_between(assemble_sheet(a.nodes, a.triangles, SHEET),
                            a.hole_rims["drill:H3"], a.hole_rims["drill:H1"])[0]
    f = layer(mesh, "F.Cu")
    on_pad = np.unique(f.triangles[f.pad_of == f.pad_ids.index("pad:F.Cu:R1.2")])   # exact pad
    rb = resistance_between(assemble_sheet(f.nodes, f.triangles, SHEET),
                            on_pad, f.hole_rims["drill:H1"])[0]
    return {"A": ra, "B": rb}


def test_default_sizes_are_within_gate_5_on_the_thermal_board(thermal, meshes):
    board, _ = thermal
    coarse, fine = paths(board, meshes[1.0]), paths(board, meshes[0.5])
    for k in "AB":
        assert abs(coarse[k] / CONVERGED[k] - 1) < 0.01, (k, coarse[k])
        # Halving the sizes moves the result towards the converged value.
        assert abs(fine[k] / CONVERGED[k] - 1) < abs(coarse[k] / CONVERGED[k] - 1), k


@pytest.mark.parametrize("scale", [1.0, 0.5])
def test_copper_ring_against_the_exact_formula(thermal, scale):
    base, _ = thermal
    cx, cy, b, d = 0.2, 0.1, 5e-3, 0.3e-3
    a = d / 2 + PLATING
    disk = CopperFeature("test:disk", "GND", "F.Cu", "via", Point(cx, cy).buffer(b, quad_segs=512),
                         drill_id="drill:R")
    drill = DrillFeature("drill:R", (cx, cy), d, "plated", "F.Cu", "B.Cu", "via")
    board = replace(base, features=(disk,), pads=(), drills=(drill,))
    (L,) = mesh_net(clean_copper(board, PLATING), board, "GND", MeshSizes(scale=scale)).layers
    outer = np.flatnonzero(np.hypot(L.nodes[:, 0] - cx, L.nodes[:, 1] - cy) > b * (1 - 1e-6))
    r = resistance_between(assemble_sheet(L.nodes, L.triangles, SHEET), L.hole_rims["drill:R"],
                           outer)[0]
    exact = math.log(b / a) / (2 * math.pi * SHEET)
    assert abs(r / exact - 1) < 0.0015, r / exact


def test_every_piece_is_meshed_exactly_and_hole_edges_are_on_the_holes(thermal, meshes):
    board, clean = thermal
    drills = {d.id: d for d in board.drills}
    mesh = meshes[1.0]
    assert [L.layer_id for L in mesh.layers] == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    for L in mesh.layers:
        pieces = [p for p in clean.pieces if p.net_id == "GND" and p.layer_id == L.layer_id]
        assert L.piece_ids == tuple(p.id for p in pieces)
        area, _, _ = triangle_sizes(L)
        assert area.min() > 0
        for k, piece in enumerate(pieces):
            # The mesh covers the piece as rounded to the 10 nm grid: at most a grid step
            # of copper along its outline.
            g = piece.geometry
            assert abs(area[L.piece_of == k].sum() - g.area) < g.length * GRID_M
        plated = {h.drill_id for h in clean.cut_holes if h.layer_id == L.layer_id
                  and any(pid in L.piece_ids for pid in h.piece_ids)}
        assert set(L.hole_rims) == plated
        for drill_id, nodes in L.hole_rims.items():
            d = drills[drill_id]
            radius = d.diameter_m / 2 + PLATING
            r = np.hypot(*(L.nodes[nodes] - d.position_m).T)
            # On the hole outline: vertices on the circle, chords at most 1 µm inside,
            # moved by at most the 10 nm grid.
            assert r.max() < radius + GRID_M and r.min() > radius - 1e-6 - GRID_M, drill_id
            assert len(nodes) >= 20


def test_sizes_follow_the_rules_and_halving_the_scale_halves_them(thermal, meshes):
    board, _ = thermal
    via = next(d for d in board.drills if d.id == "drill:H1")
    sizes = {}
    for scale, mesh in meshes.items():
        L = layer(mesh, "In1.Cu")
        _, h, centre = triangle_sizes(L)
        far = np.array([min(Point(x, y).distance(Point(d.position_m)) for d in board.drills)
                        for x, y in centre])
        assert np.median(h[far > 8e-3]) == pytest.approx(0.5e-3 * scale, rel=0.1)
        assert h.max() < 1.25 * 0.5e-3 * scale
        ring = np.hypot(*(centre - via.position_m).T)
        sizes[scale] = np.median(h[(ring > 0.6e-3) & (ring < 1.0e-3)])
        if scale == 1.0:
            # 0.6 to 1 mm from a 0.3 mm via: hole side + 0.075 x distance, far below 0.5 mm.
            assert sizes[scale] < 0.15e-3
    # Halving the scale halves the size at the same distance from the via.
    assert 0.4 < sizes[0.5] / sizes[1.0] < 0.6
    counts = [len(layer(meshes[s], "In1.Cu").triangles) for s in (1.0, 0.5)]
    assert counts[0] < 40000 and 3 < counts[1] / counts[0] < 5


def test_keep_dir_keeps_the_gmsh_files(thermal, tmp_path):
    board, clean = thermal
    mesh = mesh_net(clean, board, "GND", MeshSizes(scale=2.0), keep_dir=tmp_path / "mesh")
    files = sorted(p.name for p in (tmp_path / "mesh").iterdir())
    assert files == [f"layer{n}.{e}" for n in range(4) for e in ("geo", "msh")]
    assert "4." in mesh.gmsh_version


def test_rejections(thermal):
    board, clean = thermal
    with pytest.raises(ValueError, match="no copper pieces to mesh"):
        mesh_net(clean, board, "NOT_A_NET")
    with pytest.raises(ValueError, match="no copper pieces to mesh"):
        mesh_net(clean, board, None)
    with pytest.raises(ValueError, match="clean must be a CleanCopper"):
        mesh_net(board, board, "GND")
    with pytest.raises(ValueError, match="board must be the BoardModel"):
        mesh_net(clean, clean, "GND")
    for field, value in (("max_m", 0), ("growth", -1), ("growth", 2), ("per_width", 0.5),
                         ("pad_divisions", 0.5), ("scale", float("nan")), ("scale", True)):
        with pytest.raises(ValueError, match=f"MeshSizes.{field}"):
            MeshSizes(**{field: value})


@pytest.mark.parametrize("scale", [1.0, 0.5])
def test_plain_copper_without_holes_or_pads_follows_the_maximum(thermal, scale):
    # No hole or pad rules apply: only the maximum decides, and it must scale too.
    from shapely.geometry import box
    base, _ = thermal
    square = CopperFeature("test:square", "GND", "In1.Cu", "fill", box(0.2, 0.1, 0.21, 0.11))
    board = replace(base, features=(square,), pads=(), drills=())
    (L,) = mesh_net(clean_copper(board, PLATING), board, "GND", MeshSizes(scale=scale)).layers
    _, h, _ = triangle_sizes(L)
    assert L.hole_rims == {}
    assert np.median(h) == pytest.approx(0.5e-3 * scale, rel=0.1)


def test_every_pad_is_whole_triangles_and_an_equipotential_contact(thermal, meshes):
    board, clean = thermal
    features = {f.id: f for f in board.features}
    pads_seen = 0
    for L in meshes[1.0].layers:
        area, _, _ = triangle_sizes(L)
        expected = {f.id for f in board.features
                    if f.layer_id == L.layer_id and f.net_id == "GND" and f.kind == "pad"}
        assert set(L.pad_ids) == expected
        for j, pad_id in enumerate(L.pad_ids):
            pad = features[pad_id].geometry
            copper = unary_union([p.geometry for p in clean.pieces if p.layer_id == L.layer_id
                                  and p.net_id == "GND"]).intersection(pad)
            mine = L.pad_of == j
            # The pad's triangles cover exactly the pad's copper (minus its hole).
            assert abs(area[mine].sum() - copper.area) < (pad.length + copper.length) * GRID_M
            # The mesh follows the pad outline: the strict equipotential contact accepts it.
            region = unary_union([Polygon(L.nodes[tri]) for tri in L.triangles[mine]])
            contact = select_area_contact(L.nodes, L.triangles, region, model="equipotential")
            assert set(contact.nodes) == set(np.unique(L.triangles[mine]))
            pads_seen += 1
    assert pads_seen == 6           # GND pads: TP1 on all 4 layers, R1.2 and TP2 on F.Cu


# Thin copper must refine with the scale (review 2026-11-26): its narrow parts are found
# once at scale 1, and the cap w / per_width is multiplied by the scale. Before the fix
# the width ladder moved with the scale by exactly two √2 steps per halving, so the same
# cap won at every round and a thin track was never refined.

def test_thin_copper_is_found_at_scale_1_whatever_the_scale():
    # The narrow parts and their size at scale 1 do not depend on the scale; the size
    # fields multiply it by the scale. Before the fix the width ladder moved with the
    # scale by exactly two √2 steps per halving, so a thin track's size never changed.
    from shapely.geometry import box
    from pdnlab_dc.board_mesh import _thin_regions, _thin_samples
    from pdnlab_dc.run import SCALES
    track = box(0, 0, 10e-3, 80e-6)                                  # 10 mm x 80 µm
    found = {s: _thin_samples(_thin_regions(track, MeshSizes(scale=s)), MeshSizes(scale=s))
             for s in SCALES}
    for s in SCALES[1:]:
        assert found[s] == found[1.0]
    # The first ladder width above 80 µm is 2 mm / √2^9 = 88.4 µm: 22.1 µm, 4 across.
    cap, points = min(found[1.0], key=lambda f: f[0])
    assert cap == pytest.approx(2e-3 / math.sqrt(2) ** 9 / 4, rel=1e-9)
    assert len(points) >= track.length / cap


def test_a_thin_neck_into_wide_copper_refines_inside_the_neck(thermal):
    # Two 3 mm squares joined by a 1 mm x 80 µm neck. A whole-board triangle count can
    # hide an unchanged neck, so the sizes are measured inside the neck itself.
    from shapely.geometry import box
    base, _ = thermal
    x0, y0, w = 0.2, 0.1, 80e-6
    outline = unary_union([box(x0, y0, x0 + 3e-3, y0 + 3e-3),
                           box(x0 + 3e-3, y0 + 1.5e-3 - w / 2, x0 + 4e-3, y0 + 1.5e-3 + w / 2),
                           box(x0 + 4e-3, y0, x0 + 7e-3, y0 + 3e-3)])
    neck = box(x0 + 3.2e-3, y0 + 1.5e-3 - w / 2, x0 + 3.8e-3, y0 + 1.5e-3 + w / 2)
    feature = CopperFeature("test:neck", "GND", "In1.Cu", "fill", outline)
    board = replace(base, features=(feature,), pads=(), drills=())
    clean = clean_copper(board, PLATING)
    inside, plate = {}, {}
    for scale in (1.0, 0.5, 0.25):
        (L,) = mesh_net(clean, board, "GND", MeshSizes(scale=scale)).layers
        _, h, centre = triangle_sizes(L)
        in_neck = np.array([neck.contains(Point(x, y)) for x, y in centre])
        far = np.abs(centre[:, 0] - (x0 + 3.5e-3)) > 2e-3            # plate, away from the neck
        inside[scale], plate[scale] = np.median(h[in_neck]), np.median(h[far])
        assert in_neck.sum() >= 4 * 0.6e-3 / inside[scale]            # 4 across, the full length
    # The neck: 4 triangles across at scale 1 (cap 22 µm), and it halves with the scale.
    assert inside[1.0] < w / 3
    assert 0.4 < inside[0.5] / inside[1.0] < 0.6
    assert 0.4 < inside[0.25] / inside[0.5] < 0.6
    # The plates grow back out from the neck (growth 0.075, like a hole): below the
    # maximum near the junction, and halving with the scale too.
    assert plate[1.0] < 0.5e-3
    assert 0.4 < plate[0.5] / plate[1.0] < 0.6

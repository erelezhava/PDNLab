"""Meshing one net of a cleaned board (board_mesh.mesh_net), checked by measurement.

Accuracy, measured 2026-11-22 (docs/DECISIONS.md):
* A copper ring (0.3 mm via plus 25 µm plating to a 5 mm radius edge) against the
  exact ln(b/a) / (2 pi sigma t): within 0.15 % at scale 1; refining settles about
  0.1 % high, the 1 µm hole rule (vertices on the circle make the hole slightly
  smaller), so it leans to a slightly higher drop, never lower.
* Two paths on the thermal board's GND net, against their converged values (scale
  0.125, over a million triangles per layer; consecutive halvings changed them by
  0.12 % and 0.001 %): (A) In1.Cu, TP1's hole edge through four 0.5 mm spokes and the
  plane to a via's hole edge, 0.8571 mOhm; (B) F.Cu, R1.2's SMD pad through three
  0.3 mm spokes to the same via, 0.9122 mOhm. Default sizes must be within release
  gate 5's 1 % at scale 1.
Needs a gmsh executable (skipped without one, like the other Gmsh tests).
"""

import math
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from shapely import contains_xy
from shapely.geometry import Point

from pdnlab_dc.board import CopperFeature, DrillFeature
from pdnlab_dc.board_mesh import MeshSizes, mesh_net
from pdnlab_dc.copper_cleanup import clean_copper
from pdnlab_dc.fem import assemble_sheet, resistance_between
from pdnlab_dc.ipc2581_board import read_ipc2581_board

pytestmark = pytest.mark.skipif(shutil.which("gmsh") is None, reason="external Gmsh not installed")

FIXTURE = Path(__file__).parent / "fixtures" / "thermal_relief" / "thermal_relief.xml"
PLATING = 25e-6
SHEET = 5.8e7 * 35e-6            # siemens; only ratios matter here
CONVERGED = {"A": 0.8571e-3, "B": 0.9122e-3}


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
    pad = next(f for f in board.features if f.id == "pad:F.Cu:R1.2").geometry
    a = layer(mesh, "In1.Cu")
    ra = resistance_between(assemble_sheet(a.nodes, a.triangles, SHEET),
                            a.hole_rims["drill:H3"], a.hole_rims["drill:H1"])[0]
    f = layer(mesh, "F.Cu")
    on_pad = np.flatnonzero(contains_xy(pad, f.nodes[:, 0], f.nodes[:, 1]))
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
            # The mesh covers the piece exactly: its boundary is the piece's outline.
            assert area[L.piece_of == k].sum() == pytest.approx(piece.geometry.area, rel=1e-9)
        plated = {h.drill_id for h in clean.cut_holes if h.layer_id == L.layer_id
                  and any(pid in L.piece_ids for pid in h.piece_ids)}
        assert set(L.hole_rims) == plated
        for drill_id, nodes in L.hole_rims.items():
            d = drills[drill_id]
            radius = d.diameter_m / 2 + PLATING
            r = np.hypot(*(L.nodes[nodes] - d.position_m).T)
            # On the hole outline: vertices on the circle, chords at most 1 µm inside.
            assert r.max() < radius + 1e-9 and r.min() > radius - 1.01e-6, drill_id
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
                         ("scale", float("nan")), ("scale", True)):
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

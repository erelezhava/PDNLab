"""Two independently meshed layers connected only by a finite barrel branch."""

import shutil

import numpy as np
import pytest
import scipy.sparse as sp

from pdnlab_dc.fem import add_resistors, assemble_sheet, resistance_between, tie_nodes
from pdnlab_dc.gmsh_mesh import read_sheet_mesh, run_gmsh, write_annulus_geo
from pdnlab_dc.vias import barrel_resistance


@pytest.mark.skipif(shutil.which("gmsh") is None, reason="external Gmsh not installed")
@pytest.mark.parametrize("ri,plating,length,outer,thickness,h", [
    (0.15e-3, 25e-6, 1.6e-3, (1.5e-3, 1.5e-3), (35e-6, 35e-6), 0.05e-3),
    (0.15e-3, 25e-6, 0.05e-3, (1e-3, 2e-3), (18e-6, 70e-6), 0.05e-3),
    (1e-3, 100e-6, 0.005e-3, (5e-3, 3e-3), (18e-6, 35e-6), 0.25e-3),
])
def test_two_rings_and_barrel(tmp_path, ri, plating, length, outer, thickness, h):
    rho, ro = 1.724e-8, ri + plating
    meshes, matrices, discrete_rings = [], [], []
    for i, (b, t) in enumerate(zip(outer, thickness)):
        geo = tmp_path / f"layer{i}.geo"
        write_annulus_geo(geo, ro, b, h)
        run_gmsh(geo, geo.with_suffix(".msh"))
        mesh = read_sheet_mesh(geo.with_suffix(".msh"), boundaries=("inner", "outer"))
        matrix = assemble_sheet(mesh.nodes, mesh.triangles, t / rho)
        meshes.append(mesh)
        matrices.append(matrix)
        discrete_rings.append(resistance_between(matrix, mesh.boundaries["outer"], mesh.boundaries["inner"])[0])

    offset = len(meshes[0].nodes)
    sheets = sp.block_diag(matrices, format="csr")
    groups = [meshes[0].boundaries["inner"], meshes[1].boundaries["inner"] + offset]
    reduced, mapping = tie_nodes(sheets, groups)
    top, bottom = [int(mapping[group[0]]) for group in groups]
    assert top != bottom
    assert reduced[top, bottom] == 0  # No pre-existing interlayer connection.
    r_barrel = barrel_resistance(length, ri, ro, rho)
    network = add_resistors(reduced, [[top, bottom]], r_barrel)
    high = mapping[meshes[0].boundaries["outer"]]
    low = mapping[meshes[1].boundaries["outer"] + offset]
    r, v = resistance_between(network, high, low)
    # Independent analytic reference, including barrel area and both rings.
    ref_barrel = rho * length / (np.pi * (ro**2 - ri**2))
    ref_rings = [rho * np.log(b / ro) / (2 * np.pi * t) for b, t in zip(outer, thickness)]
    reference = sum(ref_rings) + ref_barrel
    assert r == pytest.approx(reference, rel=0.005)
    assert r == pytest.approx(sum(discrete_rings) + r_barrel, rel=1e-9)

    current = 1 / r
    assert 1 > v[top] > v[bottom] > 0
    assert v[top] - v[bottom] == pytest.approx(ref_barrel / reference, rel=0.005)
    assert (v[top] - v[bottom]) / r_barrel == pytest.approx(current, rel=1e-9)
    # Each layer's sheet drop must be resolved even if the barrel dominates.
    assert (1 - v[top]) / current == pytest.approx(ref_rings[0], rel=0.005)
    assert v[bottom] / current == pytest.approx(ref_rings[1], rel=0.005)
    expanded = v[mapping]
    for group in groups:
        assert np.ptp(expanded[group]) == 0
    sheet_current = reduced @ v
    assert sheet_current[top] == pytest.approx(-current, rel=1e-9)
    assert sheet_current[bottom] == pytest.approx(current, rel=1e-9)

    # A changed barrel resistance must change the total by that exact amount:
    # catches accidentally sharing a single rim node between both layers.
    doubled = add_resistors(reduced, [[top, bottom]], 2 * r_barrel)
    r_doubled, _ = resistance_between(doubled, high, low)
    assert r_doubled - r == pytest.approx(r_barrel, rel=1e-7)

"""Experiment 3: radial field, physical rims, electrical tie and h/2 convergence."""

import shutil

import numpy as np
import pytest

from pdnlab_dc.fem import assemble_sheet, resistance_between, solve_fixed_voltages, tie_nodes
from pdnlab_dc.gmsh_mesh import read_sheet_mesh, run_gmsh, write_annulus_geo


@pytest.mark.skipif(shutil.which("gmsh") is None, reason="external Gmsh not installed")
@pytest.mark.parametrize("a,b,t,h", [(1e-3, 5e-3, 35e-6, 0.25e-3), (0.15e-3, 1.5e-3, 18e-6, 0.05e-3), (3e-3, 6e-3, 70e-6, 0.4e-3)])
def test_annulus_converges_with_tied_inner_rim(tmp_path, a, b, t, h):
    rho = 1.724e-8
    reference = rho * np.log(b / a) / (2 * np.pi * t)
    errors = []
    sizes = []
    for level, mesh_size in enumerate([h, h / 2]):
        geo = tmp_path / f"ring{level}.geo"
        msh = geo.with_suffix(".msh")
        write_annulus_geo(geo, a, b, mesh_size)
        run_gmsh(geo, msh)
        mesh = read_sheet_mesh(msh, boundaries=("inner", "outer"))
        inner, outer = mesh.boundaries["inner"], mesh.boundaries["outer"]
        radii = np.linalg.norm(mesh.nodes, axis=1)
        # Independently check all four CAD arcs were collected into each rim.
        np.testing.assert_array_equal(inner, np.flatnonzero(np.isclose(radii, a, atol=1e-12, rtol=0)))
        np.testing.assert_array_equal(outer, np.flatnonzero(np.isclose(radii, b, atol=1e-12, rtol=0)))
        assert radii.min() >= a - 1e-12
        assert radii.max() <= b + 1e-12
        matrix = assemble_sheet(mesh.nodes, mesh.triangles, t / rho)
        reduced, mapping = tie_nodes(matrix, [inner])
        rim = np.unique(mapping[inner])
        assert len(rim) == 1
        assert reduced.shape[0] == len(mesh.nodes) - len(inner) + 1
        resistance, reduced_v = resistance_between(reduced, rim, mapping[outer])
        v = reduced_v[mapping]
        assert np.ptp(v[inner]) == 0
        assert np.max(np.abs(v - np.log(b / radii) / np.log(b / a))) < 0.005
        # An independent Dirichlet treatment must give the same resistance.
        direct_r, direct_v = resistance_between(matrix, inner, outer)
        assert resistance == pytest.approx(direct_r, rel=1e-10)
        np.testing.assert_allclose(v, direct_v, atol=1e-10, rtol=0)
        # Exercise a free rim driven by current: its voltage comes from the
        # solve, rather than prescribing the same voltage on every rim node.
        injected = np.zeros(reduced.shape[0])
        injected[rim] = 1.0
        current_v = solve_fixed_voltages(reduced, mapping[outer], 0, injected)
        assert current_v[rim[0]] == pytest.approx(resistance, rel=1e-10)
        errors.append(abs(resistance - reference) / reference)
        sizes.append(len(mesh.nodes))
    assert max(errors) <= 0.005
    assert errors[1] < errors[0]
    assert sizes[1] > sizes[0]


@pytest.mark.parametrize("a,b,h", [(0, 1, 0.1), (1, 1, 0.1), (2, 1, 0.1), (1, 2, 0), (np.nan, 2, 0.1), (1, np.inf, 0.1)])
def test_invalid_annulus_dimensions(tmp_path, a, b, h):
    with pytest.raises(ValueError, match="require finite"):
        write_annulus_geo(tmp_path / "bad.geo", a, b, h)

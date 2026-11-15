"""Prepared solve contract and independent synthetic electrical references."""

import numpy as np
import pytest
import scipy.sparse as sp

from pdnlab_dc import solver
from pdnlab_dc.checks import balance_checks, resistor_losses, sheet_losses
from pdnlab_dc.fem import add_resistors, assemble_sheet, tie_nodes
from pdnlab_dc.simple_meshes import rectangle_mesh
from pdnlab_dc.vias import barrel_resistance


def chain():
    return add_resistors(sp.csr_matrix((3, 3)), [[0, 1], [1, 2]], [2., 3.])


def test_reuses_one_factorization_and_snapshots_inputs(monkeypatch):
    original = solver.splu
    calls = []

    def counted(matrix):
        calls.append(matrix.shape)
        return original(matrix)

    monkeypatch.setattr(solver, "splu", counted)
    matrix, fixed = chain(), np.array([0])
    prepared = solver.prepare_fixed_voltages(matrix, fixed)
    matrix.data[:] = 0
    fixed[:] = 2
    prepared.fixed_nodes[:] = 1
    for voltage, load in [(3.3, .1), (1., .4), (-2., -.2)]:
        v = prepared.solve(voltage, [99., 0., -load])
        np.testing.assert_allclose(v, [voltage, voltage - 2*load, voltage - 5*load])
    z = prepared.transfer_resistance([1, 2], batch_size=1)
    np.testing.assert_allclose(z, [[2, 2], [2, 5]])
    assert calls == [(2, 2)]
    assert prepared.stats.factor_nnz > 0
    assert prepared.stats.sparse_storage_bytes > 0
    assert prepared.stats.preparation_s >= prepared.stats.factorization_s >= 0
    assert prepared.last_solve.max_batch_columns == 1
    assert prepared.last_solve.max_rhs_bytes == 16


@pytest.mark.parametrize("batch", [1, 2, 10])
def test_transfer_batches_match_analytic_and_direct_currents(batch):
    prepared = solver.prepare_fixed_voltages(chain(), [0])
    z = prepared.transfer_resistance([2, 0, 1, 2], [2, 1, 0], batch_size=batch)
    np.testing.assert_allclose(z, [[5, 0, 2, 5], [2, 0, 2, 2], [0, 0, 0, 0]])
    v = prepared.solve(0., [0., -.1, -.2])
    np.testing.assert_allclose(-v[[2, 1, 0]], z[:, [2, 0]] @ [.1, .2])


def test_transfer_limits_full_field_rhs_columns(monkeypatch):
    prepared = solver.prepare_fixed_voltages(chain(), [0])
    original = prepared._solve_rhs
    shapes = []

    def measured(rhs):
        shapes.append(rhs.shape)
        return original(rhs)

    monkeypatch.setattr(prepared, "_solve_rhs", measured)
    prepared.transfer_resistance([1, 2, 1, 2, 1], batch_size=2)
    assert shapes == [(2, 2), (2, 2), (2, 1)]


def test_multiple_fixed_values_preserve_input_order():
    prepared = solver.prepare_fixed_voltages(chain(), [2, 0])
    np.testing.assert_allclose(prepared.solve([5., 0.]), [0., 2., 5.])


def test_all_fixed_has_no_factorization(monkeypatch):
    def forbidden(*args):
        pytest.fail("all-fixed network must not factor")
    monkeypatch.setattr(solver, "splu", forbidden)
    prepared = solver.prepare_fixed_voltages(chain(), [2, 0, 1])
    np.testing.assert_array_equal(prepared.solve([4, 2, 3]), [2, 3, 4])
    np.testing.assert_array_equal(prepared.transfer_resistance([0, 2]), np.zeros((2, 2)))
    assert prepared.stats.factor_nnz == 0


@pytest.mark.parametrize("matrix,fixed", [
    (sp.csr_matrix((0, 0)), [0]),
    (sp.csr_matrix((2, 3)), [0]),
    (sp.eye(2), []), (sp.eye(2), [0, 0]), (sp.eye(2), [.5]),
    (sp.eye(2), [2]), (sp.eye(2), [0]),
    (sp.csr_matrix([[np.nan]]), [0]),
    (sp.csr_matrix([[1., 1., 1.]] * 3), [0]),
])
def test_invalid_preparation(matrix, fixed):
    with pytest.raises(ValueError):
        solver.prepare_fixed_voltages(matrix, fixed)


@pytest.mark.parametrize("voltage,current", [
    (np.nan, None), ([1, 2], None), ([[1]], None),
    (0, [0]), (0, [0, np.inf, 0]),
])
def test_invalid_load_case(voltage, current):
    with pytest.raises(ValueError):
        solver.prepare_fixed_voltages(chain(), [0]).solve(voltage, current)


@pytest.mark.parametrize("batch", [0, -1, 1.5, True])
def test_invalid_batch_size(batch):
    with pytest.raises(ValueError, match="batch_size"):
        solver.prepare_fixed_voltages(chain(), [0]).transfer_resistance([2], batch_size=batch)


@pytest.mark.parametrize("inject,observe", [([], None), ([3], None), ([.5], None), ([1], [])])
def test_invalid_transfer_nodes(inject, observe):
    with pytest.raises(ValueError):
        solver.prepare_fixed_voltages(chain(), [0]).transfer_resistance(inject, observe)


@pytest.mark.parametrize("nx,ny", [(8, 3), (16, 6)])
def test_three_layer_branching_strip_barrels_and_independent_balances(nx, ny):
    # One rim per layer; different layer rims must never be merged together.
    rho, length, width, thickness = 1.724e-8, .05, .005, 35e-6
    nodes, triangles, left, right = rectangle_mesh(length, width, nx, ny, jitter=.2)
    sheet = assemble_sheet(nodes, triangles, thickness/rho)
    n = len(nodes)
    sheets = sp.block_diag([sheet]*3, format="csr")
    groups = [edge + k*n for k in range(3) for edge in (left, right)]
    reduced, mapping = tie_nodes(sheets, groups)
    rims = [int(mapping[left[0] + k*n]) for k in range(3)]
    terminals = [int(mapping[right[0] + k*n]) for k in range(3)]
    pairs = np.array([[rims[0], rims[1]], [rims[1], rims[2]]])
    barrel = barrel_resistance(.0008, .00015, .000175, rho)
    resistances = np.array([barrel, barrel])
    matrix = add_resistors(reduced, pairs, resistances)
    prepared = solver.prepare_fixed_voltages(matrix, [terminals[0]])
    strip = rho*length/(width*thickness)
    for scale in (1., 2.):
        current = np.zeros(len(matrix.indptr)-1)
        current[terminals[1:]] = -scale*np.array([.1, .2])
        v = prepared.solve(3.3, current)
        expected = scale*np.array([.3*(strip+barrel)+.1*strip,
                                    .3*(strip+barrel)+.2*(barrel+strip)])
        np.testing.assert_allclose(3.3-v[terminals[1:]], expected, rtol=1e-6)
        full = v[mapping]
        heat = sum(sheet_losses(nodes, triangles, thickness/rho,
                                full[k*n:(k+1)*n]).sum() for k in range(3))
        branch = (v[pairs[:, 0]] - v[pairs[:, 1]]) / resistances
        np.testing.assert_allclose(branch, scale*np.array([.3, .2]), rtol=1e-6)
        sheet_current = sheets @ full
        rim_sheet = [sheet_current[left+k*n].sum() for k in range(3)]
        # All currents positive leaving the rim: sheet contribution + barrels.
        rims_kcl = {"top": [rim_sheet[0], branch[0]],
                    "middle": [rim_sheet[1], -branch[0], branch[1]],
                    "bottom": [rim_sheet[2], -branch[1]]}
        checks = balance_checks(matrix, v, [terminals[0]], heat,
                                resistor_losses(v, pairs, resistances).sum(),
                                current, rims_kcl)
        assert checks["passed"], checks

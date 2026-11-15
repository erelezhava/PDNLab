"""Conservation must not depend on the arbitrary voltage origin."""

import numpy as np
import pytest
import scipy.sparse as sp

from pdnlab_dc.checks import balance_checks, sheet_losses
from pdnlab_dc.fem import add_resistors, assemble_sheet
from pdnlab_dc.simple_meshes import rectangle_mesh
from pdnlab_dc.solver import prepare_fixed_voltages


@pytest.fixture(scope="module")
def sheet():
    nodes, triangles, fixed, _ = rectangle_mesh(.1, .02, 160, 40)
    g = 35e-6 / 1.724e-8
    matrix = assemble_sheet(nodes, triangles, g)
    prepared = prepare_fixed_voltages(matrix, fixed)
    free = np.setdiff1d(np.arange(len(nodes)), fixed)
    loads = free[np.linspace(0, len(free)-1, 24, dtype=int)]
    return nodes, triangles, fixed, g, matrix, prepared, loads


@pytest.mark.parametrize("reference", [0., 3.3, -3.3, 12., 48.])
@pytest.mark.parametrize("amps", [.08, 1e-5, 0.])
def test_sheet_conservation_at_shifted_references(sheet, reference, amps):
    nodes, triangles, fixed, g, matrix, prepared, loads = sheet
    current = np.zeros(len(nodes))
    current[loads] = -amps
    base = prepared.solve(0., current)
    voltage = prepared.solve(reference, current)
    np.testing.assert_allclose(voltage-reference, base, rtol=0, atol=1e-13)
    loss = sheet_losses(nodes, triangles, g, voltage).sum()
    checks = balance_checks(matrix, voltage, fixed, loss, injected_current=current)
    assert checks["passed"], checks
    if amps == 0.:
        np.testing.assert_array_equal(voltage, np.full(len(nodes), reference))
        assert checks["current_balance"]["source_a"] == 0.
        assert loss == 0.
    # A wrong nonzero load must still fail, at every reference.
    if amps:
        wrong = current.copy()
        wrong[loads[0]] = 0.
        assert not balance_checks(matrix, voltage, fixed, loss,
                                  injected_current=wrong)["passed"]


def test_independent_components_have_independent_origins():
    # Two separately referenced resistors, source input order deliberately mixed.
    matrix = add_resistors(sp.csr_matrix((4, 4)), [[0, 1], [2, 3]], [2., 3.])
    prepared = prepare_fixed_voltages(matrix, [2, 0])
    current = np.array([0., -.1, 0., -.2])
    voltage = prepared.solve([48., -3.3], current)
    np.testing.assert_allclose(voltage, [-3.3, -3.5, 48., 47.4])
    checks = balance_checks(matrix, voltage, [2, 0], .1**2*2+.2**2*3,
                            injected_current=current)
    assert checks["passed"], checks


def test_component_imbalance_cannot_cancel_between_networks():
    matrix = add_resistors(sp.csr_matrix((4, 4)), [[0, 1], [2, 3]], 1.)
    # Both voltages imply 1 A per network; prescribed sinks instead draw 0/2 A.
    # Global KCL and reference-normalized power both happen to balance.
    checks = balance_checks(matrix, [3.3, 2.3, 48., 47.], [0, 2], 2.,
                            injected_current=[0., 0., 0., -2.])
    assert checks["current_balance"]["passed"]
    assert checks["power_balance"]["passed"]
    assert not checks["passed"]


@pytest.mark.parametrize("check", [False, True])
def test_material_row_sum_defect_is_rejected(check):
    matrix = sp.csr_matrix([[1.01, -1.], [-1., 1.]])
    with pytest.raises(ValueError, match="row sums"):
        if check:
            balance_checks(matrix, [3.3, 2.3], [0], 1., injected_current=[0., -1.])
        else:
            prepare_fixed_voltages(matrix, [0])


@pytest.mark.parametrize("reference", [0., 3.3, -48.])
def test_different_fixed_values_and_free_injection(reference):
    matrix = add_resistors(sp.csr_matrix((3, 3)), [[0, 1], [1, 2]], [2., 3.])
    prepared = prepare_fixed_voltages(matrix, [2, 0])
    v = prepared.solve(np.array([5., 0.])+reference, [0., -.25, 0.])
    # (V1 - 0)/2 + (V1 - 5)/3 = -0.25 -> V1 = 1.7 V.
    np.testing.assert_allclose(v-reference, [0., 1.7, 5.], rtol=0, atol=1e-13)
    checks = balance_checks(matrix, v, [2, 0], 1.7**2/2+(1.7-5)**2/3,
                            injected_current=[0., -.25, 0.])
    assert checks["passed"], checks

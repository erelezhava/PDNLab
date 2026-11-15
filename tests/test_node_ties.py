"""Equipotential ties preserve voltage constraints, summed KCL and energy."""

import numpy as np
import pytest
import scipy.sparse as sp

from pdnlab_dc.fem import solve_fixed_voltages, tie_nodes


def test_tied_floating_terminal_shares_current_and_preserves_energy():
    # Ground -- 1 ohm -- node 1; ground -- 2 ohm -- node 2.
    # Tie 1 and 2, feed 3 A into their common terminal: V = 2 V.
    matrix = sp.csr_matrix([[1.5, -1, -0.5], [-1, 1, 0], [-0.5, 0, 0.5]])
    reduced, mapping = tie_nodes(matrix, [[1, 2]])
    injected = np.bincount(mapping, weights=[0, 3, 0])
    v_reduced = solve_fixed_voltages(reduced, [mapping[0]], 0, injected)
    v = v_reduced[mapping]
    np.testing.assert_allclose(v, [0, 2, 2])
    currents = matrix @ v
    np.testing.assert_allclose(currents, [-3, 2, 1])
    np.testing.assert_allclose(np.bincount(mapping, weights=currents), reduced @ v_reduced)
    assert v @ matrix @ v == pytest.approx(v_reduced @ reduced @ v_reduced)
    np.testing.assert_allclose(reduced.toarray(), reduced.T.toarray())


def test_two_disjoint_rims_remain_distinct():
    matrix = sp.diags([1, 2, 3, 4], format="csr", dtype=float)
    reduced, mapping = tie_nodes(matrix, [[0, 1], [2, 3]])
    assert mapping[0] == mapping[1]
    assert mapping[2] == mapping[3]
    assert mapping[0] != mapping[2]
    np.testing.assert_allclose(reduced.toarray(), [[3, 0], [0, 7]])


def test_no_ties_preserves_matrix():
    matrix = sp.csr_matrix([[1, -1], [-1, 1]])
    reduced, mapping = tie_nodes(matrix, [])
    np.testing.assert_array_equal(mapping, [0, 1])
    np.testing.assert_array_equal(reduced.toarray(), matrix.toarray())


@pytest.mark.parametrize("groups", [[[]], [[0, 0]], [[0, 1], [1, 2]], [[-1]], [[3]], [[0.5]]])
def test_bad_ties_rejected(groups):
    with pytest.raises(ValueError):
        tie_nodes(sp.eye(3, format="csr"), groups)

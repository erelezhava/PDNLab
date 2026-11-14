"""Lumped branches must stamp a passive network without changing the input."""

import numpy as np
import pytest
import scipy.sparse as sp

from pdnlab_dc.fem import add_resistors, resistance_between
from pdnlab_dc.vias import barrel_resistance


def test_series_resistors_and_input_unchanged():
    base = sp.csr_matrix((3, 3), dtype=float)
    network = add_resistors(base, [[0, 1], [1, 2]], [2, 3])
    resistance, v = resistance_between(network, [0], [2])
    assert resistance == pytest.approx(5)
    np.testing.assert_allclose(v, [1, 0.6, 0])
    assert base.nnz == 0
    np.testing.assert_allclose(network.toarray(), network.T.toarray())
    np.testing.assert_allclose(np.asarray(network.sum(axis=1)), 0, atol=1e-15)


def test_parallel_resistors_and_reversed_terminals():
    network = add_resistors(sp.csr_matrix((2, 2)), [[0, 1], [1, 0]], 4)
    assert resistance_between(network, [0], [1])[0] == pytest.approx(2)


@pytest.mark.parametrize("pairs,r", [([[0, 0]], 1), ([[0, 2]], 1), ([[0.5, 1]], 1), ([[0, 1]], 0), ([[0, 1]], -1), ([[0, 1]], np.nan), ([[0, 1]], np.inf), ([[0, 1]], 1e-320), ([[0, 1, 0]], 1)])
def test_invalid_branches(pairs, r):
    with pytest.raises(ValueError):
        add_resistors(sp.csr_matrix((2, 2)), pairs, r)


def test_barrel_against_independent_area_and_scaling():
    rho, length, ri, ro = 1.724e-8, 1.6e-3, 0.15e-3, 0.175e-3
    reference = rho * length / (np.pi * (ro**2 - ri**2))
    r = barrel_resistance(length, ri, ro, rho)
    assert r == pytest.approx(reference, rel=1e-14)
    assert barrel_resistance(2 * length, ri, ro, rho) == pytest.approx(2 * r)
    assert barrel_resistance(length, ri, ro, 2 * rho) == pytest.approx(2 * r)
    assert barrel_resistance(length, ri, ro + 25e-6, rho) < r


@pytest.mark.parametrize("length,ri,ro,rho", [(0, 1, 2, 1), (-1, 1, 2, 1), (1, 0, 2, 1), (1, 2, 1, 1), (1, 1, 1, 1), (1, 1, 2, 0), (np.nan, 1, 2, 1), (1, 1, np.inf, 1)])
def test_invalid_barrel_geometry(length, ri, ro, rho):
    with pytest.raises(ValueError):
        barrel_resistance(length, ri, ro, rho)

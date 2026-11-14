"""Reject invalid models before they can produce plausible numerical results."""

import numpy as np
import pytest

from pdnlab_dc.fem import assemble_sheet, resistance_between, solve_fixed_voltages
from pdnlab_dc.simple_meshes import rectangle_mesh


@pytest.mark.parametrize("g", [-1, 0, np.nan, np.inf])
def test_invalid_conductance(g):
    with pytest.raises(ValueError, match="conductance"):
        assemble_sheet([[0, 0], [1, 0], [0, 1]], [[0, 1, 2]], g)


@pytest.mark.parametrize("triangles", [[[0.1, 1, 2]], [[-1, 1, 2]], [[0, 1, 3]], [[0, 1]], []])
def test_invalid_triangle_indices(triangles):
    with pytest.raises(ValueError, match="triangles"):
        assemble_sheet([[0, 0], [1, 0], [0, 1]], triangles, 1)


def test_nonfinite_coordinates():
    with pytest.raises(ValueError, match="finite"):
        assemble_sheet([[0, 0], [np.nan, 0], [0, 1]], [[0, 1, 2]], 1)


def test_floating_island_rejected_and_can_be_anchored_separately():
    nodes = [[0, 0], [1, 0], [0, 1], [2, 0], [3, 0], [2, 1]]
    matrix = assemble_sheet(nodes, [[0, 1, 2], [3, 4, 5]], 1)
    with pytest.raises(ValueError, match="unanchored"):
        solve_fixed_voltages(matrix, [0], 1)
    v = solve_fixed_voltages(matrix, [0, 3], [1, 2])
    np.testing.assert_allclose(v, [1, 1, 1, 2, 2, 2])


@pytest.mark.parametrize("high,low", [([], [1]), ([0], []), ([0], [0]), ([-1], [1]), ([0.5], [1])])
def test_invalid_terminals(high, low):
    nodes, triangles, _, _ = rectangle_mesh(1, 1, 1, 1)
    matrix = assemble_sheet(nodes, triangles, 1)
    with pytest.raises(ValueError):
        resistance_between(matrix, high, low)


def test_injected_current_strip():
    nodes, triangles, left, right = rectangle_mesh(2, 1, 8, 4)
    matrix = assemble_sheet(nodes, triangles, 1)
    # Trapezoidal edge integration: the two corner nodes get half weight.
    current = np.zeros(len(nodes))
    current[left] = 1 / (len(left) - 1)
    current[left[[0, -1]]] *= 0.5
    v = solve_fixed_voltages(matrix, right, 0, current)
    np.testing.assert_allclose(v, 2 - nodes[:, 0], atol=1e-12)
    free = np.setdiff1d(np.arange(len(nodes)), right)
    np.testing.assert_allclose((matrix @ v)[free], current[free], atol=1e-12)


@pytest.mark.parametrize("current", [[1], [np.nan] * 4])
def test_invalid_current_vector(current):
    nodes, triangles, left, _ = rectangle_mesh(1, 1, 1, 1)
    with pytest.raises(ValueError, match="injected_current"):
        solve_fixed_voltages(assemble_sheet(nodes, triangles, 1), left, 0, current)

"""Phase 0, experiment 1: plain rectangle against R = rho * L / (W * t).

Linear elements represent a uniform field exactly, so the mesh does not
matter and any error above rounding is a bug. The limit is 1e-6 relative.
"""

import numpy as np
import pytest

from pdnlab_dc.fem import assemble_sheet, resistance_between
from pdnlab_dc.simple_meshes import rectangle_mesh

RHO_COPPER = 1.724e-8  # ohm metre, annealed copper at 20 C
LIMIT = 1e-6


def strip_reference(rho, length, width, thickness):
    return rho * length / (width * thickness)


def test_two_triangles_written_by_hand():
    # 50 mm x 5 mm strip of 35 um copper, cut into two triangles.
    length, width, thickness = 50e-3, 5e-3, 35e-6
    nodes = [(0.0, 0.0), (length, 0.0), (length, width), (0.0, width)]
    triangles = [(0, 1, 2), (0, 2, 3)]
    matrix = assemble_sheet(nodes, triangles, thickness / RHO_COPPER)

    resistance, _ = resistance_between(matrix, high_nodes=[0, 3], low_nodes=[1, 2])

    reference = strip_reference(RHO_COPPER, length, width, thickness)
    assert abs(resistance - reference) / reference <= LIMIT


@pytest.mark.parametrize(
    "length, width, thickness, nx, ny, diagonals, jitter",
    [
        (50e-3, 5e-3, 35e-6, 1, 1, "same", 0.0),
        (50e-3, 5e-3, 35e-6, 10, 3, "same", 0.0),
        (50e-3, 5e-3, 35e-6, 10, 3, "alternate", 0.0),
        (50e-3, 5e-3, 35e-6, 40, 8, "alternate", 0.2),
        (100e-3, 0.2e-3, 18e-6, 200, 2, "alternate", 0.2),  # thin trace
        (2e-3, 80e-3, 70e-6, 4, 60, "alternate", 0.2),  # short and wide
        (200e-3, 200e-3, 35e-6, 120, 120, "alternate", 0.2),  # plane-sized
    ],
)
def test_rectangle_matches_strip_formula(length, width, thickness, nx, ny, diagonals, jitter):
    nodes, triangles, left, right = rectangle_mesh(length, width, nx, ny, diagonals, jitter)
    matrix = assemble_sheet(nodes, triangles, thickness / RHO_COPPER)

    resistance, v = resistance_between(matrix, high_nodes=left, low_nodes=right)

    reference = strip_reference(RHO_COPPER, length, width, thickness)
    assert abs(resistance - reference) / reference <= LIMIT

    # The voltage must fall linearly along the strip at every node.
    expected_v = 1.0 - nodes[:, 0] / length
    assert np.max(np.abs(v - expected_v)) <= LIMIT


def test_triangle_orientation_does_not_matter():
    nodes, triangles, left, right = rectangle_mesh(50e-3, 5e-3, 10, 3)
    flipped = triangles[:, ::-1]
    g = 35e-6 / RHO_COPPER

    r_normal, _ = resistance_between(assemble_sheet(nodes, triangles, g), left, right)
    r_flipped, _ = resistance_between(assemble_sheet(nodes, flipped, g), left, right)

    assert r_flipped == pytest.approx(r_normal, rel=1e-12)


def test_zero_area_triangle_is_rejected():
    nodes = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
    with pytest.raises(ValueError, match="zero-area"):
        assemble_sheet(nodes, [(0, 1, 2)], 1.0)

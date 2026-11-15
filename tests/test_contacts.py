"""Finite contact geometry/integration, analytic accuracy and mesh dependence."""

import numpy as np
import pytest
from shapely.geometry import Point, Polygon, box

from pdnlab_dc.contacts import select_area_contact
from pdnlab_dc.network import Network
from pdnlab_dc.simple_meshes import concentric_disk_mesh, rectangle_mesh

RHO, THICKNESS = 1.724e-8, 35e-6
G = THICKNESS/RHO


@pytest.mark.parametrize("reversed_triangle", [False, True])
def test_patch_with_no_inside_vertices_has_exact_linear_shape_weights(reversed_triangle):
    nodes = np.array([[0., 0.], [1., 0.], [0., 1.]])
    tri = [[2, 1, 0]] if reversed_triangle else [[0, 1, 2]]
    selection = select_area_contact(nodes, tri, box(.2, .2, .3, .3), model="uniform_current")
    np.testing.assert_array_equal(selection.nodes, [0, 1, 2])
    np.testing.assert_allclose(selection.weights, [.5, .25, .25], atol=1e-14, rtol=0)
    assert selection.requested_area_m2 == pytest.approx(.01)
    assert selection.covered_area_m2 == pytest.approx(.01)
    assert not selection.nodes.flags.writeable
    assert not selection.weights.flags.writeable


def test_contact_hole_and_multi_piece_triangle_integrals_preserve_linear_average():
    nodes, triangles, _, _ = rectangle_mesh(1., 1., 1, 1)
    outer, hole = box(0., 0., 1., 1.), box(.1, .1, .2, .3)
    region = Polygon(outer.exterior.coords, [hole.exterior.coords])
    selection = select_area_contact(nodes, triangles, region, model="uniform_current")
    # Integral of x+2y over unit square minus 0.02 m² hole centred at (.15,.2).
    expected = (1.5-.02*(.15+2*.2))/.98
    voltages = nodes[:, 0]+2*nodes[:, 1]
    assert selection.weights @ voltages[selection.nodes] == pytest.approx(expected, rel=1e-13)
    assert selection.covered_area_m2 == pytest.approx(.98)


@pytest.mark.parametrize("region", [Point(.2, .2), Polygon(),
    Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])])
def test_invalid_region_is_rejected(region):
    with pytest.raises(ValueError, match="Polygon"):
        select_area_contact([[0, 0], [1, 0], [0, 1]], [[0, 1, 2]], region)


@pytest.mark.parametrize("model", ["unknown", "point"])
def test_invalid_model(model):
    with pytest.raises(ValueError, match="model"):
        select_area_contact([[0, 0], [1, 0], [0, 1]], [[0, 1, 2]], box(.1, .1, .2, .2), model=model)


def test_cut_equipotential_contact_requires_conforming_mesh():
    with pytest.raises(ValueError, match="conforming mesh"):
        select_area_contact([[0, 0], [1, 0], [0, 1]], [[0, 1, 2]], box(.2, .2, .3, .3))


def test_outside_copper_hole_and_overlapping_elements_are_rejected():
    nodes, triangles = [[0, 0], [1, 0], [0, 1]], [[0, 1, 2]]
    with pytest.raises(ValueError, match="covered"):
        select_area_contact(nodes, triangles, box(-.1, .1, .2, .2), model="uniform_current")
    with pytest.raises(ValueError, match="overlapping"):
        select_area_contact(nodes, triangles*2, box(.2, .2, .3, .3), model="uniform_current")
    nodes, triangles, _ = concentric_disk_mesh(1., 4, sectors=32)
    with pytest.raises(ValueError, match="covered"):
        select_area_contact(nodes, triangles[32:], box(-.1, -.1, .1, .1), model="uniform_current")


@pytest.mark.parametrize("nodes,triangles", [
    ([[0, 0], [1, 0], [0, np.nan]], [[0, 1, 2]]),
    ([[0, 0], [1, 0], [0, 1]], [[0., 1., 2.]]),
    ([[0, 0], [1, 0], [0, 1]], [[0, 1, 3]]),
    ([[0, 0], [1, 0], [2, 0]], [[0, 1, 2]]),
])
def test_invalid_mesh(nodes, triangles):
    with pytest.raises(ValueError):
        select_area_contact(nodes, triangles, box(.1, .1, .2, .2))


@pytest.mark.parametrize("offset", [(0., 0.), (.01, -.012)])
def test_positioned_electrodes_short_only_their_contact_area(offset):
    nodes, triangles, _, _ = rectangle_mesh(.05, .005, 20, 4)
    nodes += np.array(offset)
    ox, oy = offset
    net = Network()
    net.add_sheet("strip", nodes, triangles, G)
    net.add_contact("supply", "strip", box(ox+.005, oy, ox+.010, oy+.005))
    net.add_contact("load", "strip", box(ox+.035, oy, ox+.040, oy+.005))
    prepared = net.prepare(["supply"])
    metadata = prepared.contact_info
    assert metadata["load"]["requested_area_m2"] == pytest.approx(.005*.005)
    assert metadata["load"]["bounds_m"] == pytest.approx((ox+.035, oy, ox+.040, oy+.005))
    metadata["load"]["model"] = "changed by caller"
    assert prepared.contact_info["load"]["model"] == "equipotential"
    expected_r = RHO*(.035-.010)/(.005*THICKNESS)
    for source, amps in ((3.3, .2), (12., .4)):
        result = prepared.solve({"supply": source}, {"load": amps})
        assert source-result.contact_voltages_v["load"] == pytest.approx(amps*expected_r, rel=1e-6)
        assert result.sheet_losses_w["strip"] == pytest.approx(amps**2*expected_r, rel=1e-6)
        assert result.passed, result.checks
    assert prepared.transfer_resistance(["load"])[0, 0] == pytest.approx(expected_r, rel=1e-6)


def uniform_strip_case(nx, ny):
    nodes, triangles, _, _ = rectangle_mesh(.05, .005, nx, ny)
    net = Network()
    net.add_sheet("strip", nodes, triangles, G)
    net.add_contact("supply", "strip", box(0, 0, .005, .005))
    net.add_contact("load", "strip", box(.0203, 0, .0307, .005), model="uniform_current")
    prepared = net.prepare(["supply"])
    result = prepared.solve({"supply": 3.3}, {"load": 1.})
    # In 1D, I(x) falls linearly across the uniformly loaded patch. Integrating
    # I(x)^2/(gW) gives P=I²*(a-x_source+(b-a)/3)/(gW).
    expected_r = (.0203-.005+(.0307-.0203)/3)/(G*.005)
    measured_r = 3.3-result.contact_voltages_v["load"]
    assert result.terminal_currents_a["load"] == pytest.approx(-1., rel=1e-13)
    assert result.passed, result.checks
    assert abs(result.sheet_losses_w["strip"]/expected_r-1) <= .005
    with pytest.raises(ValueError, match="equipotential"):
        prepared.transfer_resistance(["load"])
    return abs(measured_r/expected_r-1)


def test_uniform_patch_on_nonconforming_mesh_converges_to_analytic_mean_voltage():
    errors = [uniform_strip_case(nx, ny) for nx, ny in ((40, 8), (80, 16), (160, 32))]
    assert max(errors) <= .005
    assert errors[2] < errors[1] < errors[0]
    print(f"uniform area contact relative errors: {errors}")


def disk_case(radial_cells, finite):
    nodes, triangles, outer = concentric_disk_mesh(.005, radial_cells)
    net = Network()
    net.add_sheet("disk", nodes, triangles, G)
    net.add_terminal("supply", "disk", outer)
    if finite:
        angles = np.arange(128)*2*np.pi/128
        region = Polygon(.00125*np.column_stack([np.cos(angles), np.sin(angles)]))
        net.add_contact("load", "disk", region)
    else:
        net.add_terminal("load", "disk", [0])
    result = net.prepare(["supply"]).solve({"supply": 0.}, {"load": 1.})
    assert result.passed, result.checks
    return -result.terminal_voltages_v["load"][0]


def test_finite_electrode_converges_while_mathematical_point_voltage_keeps_growing():
    finite = [disk_case(n, True) for n in (16, 32, 64)]
    point = [disk_case(n, False) for n in (16, 32, 64)]
    reference = RHO*np.log(4)/(2*np.pi*THICKNESS)
    errors = np.abs(np.array(finite)/reference-1)
    assert max(errors) <= .005
    assert errors[2] < errors[1] < errors[0]
    assert abs(finite[2]-finite[1]) < .01*finite[2]
    assert point[2] > point[1] > point[0] > finite[2]
    # Each halving adds approximately the annular resistance ln(2)/(2*pi*g).
    np.testing.assert_allclose(np.diff(point), RHO*np.log(2)/(2*np.pi*THICKNESS), rtol=.01)
    print(f"finite electrode R={finite}; relative errors={errors.tolist()}; point R={point}")


def test_coarse_shared_support_and_uniform_voltage_source_are_rejected():
    net = Network()
    net.add_sheet("triangle", [[0, 0], [1, 0], [0, 1]], [[0, 1, 2]], 1.)
    net.add_terminal("source", "triangle", [0])
    with pytest.raises(ValueError, match="disjoint"):
        net.add_contact("load", "triangle", box(.2, .2, .3, .3), model="uniform_current")
    assert net.prepare(["source"]).contact_info == {}
    net = Network()
    net.add_sheet("triangle", [[0, 0], [1, 0], [0, 1]], [[0, 1, 2]], 1.)
    net.add_contact("load", "triangle", box(.2, .2, .3, .3), model="uniform_current")
    with pytest.raises(ValueError, match="equipotential"):
        net.prepare(["load"])


@pytest.mark.parametrize("radius,rings,sectors", [(0., 4, 32), (np.nan, 4, 32),
    (1., 0, 32), (1., 2.5, 32), (1., True, 32), (1., 4, 2)])
def test_invalid_synthetic_disk(radius, rings, sectors):
    with pytest.raises(ValueError):
        concentric_disk_mesh(radius, rings, sectors)

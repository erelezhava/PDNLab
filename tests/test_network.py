"""Composition against analytic networks, independently integrated heat and KCL."""

import shutil

import numpy as np
import pytest

from pdnlab_dc import network as core
from pdnlab_dc import solver
from pdnlab_dc.gmsh_mesh import read_sheet_mesh, run_gmsh, write_annulus_geo
from pdnlab_dc.network import Network
from pdnlab_dc.simple_meshes import rectangle_mesh

RHO = 1.724e-8


def strip(nx=12, ny=4, weights=None):
    nodes, triangles, left, right = rectangle_mesh(.05, .005, nx, ny, jitter=.2)
    net = Network()
    net.add_sheet("copper", nodes, triangles, 35e-6/RHO)
    net.add_terminal("supply", "copper", left)
    net.add_terminal("load", "copper", right, weights=weights)
    return net, nodes, triangles, left, right


def branching(nx=8, ny=3):
    net = Network()
    thickness = [18e-6, 35e-6, 70e-6]
    lengths = [.8e-3, .05e-3]
    selections = {}
    for i, t in enumerate(thickness):
        nodes, triangles, left, right = rectangle_mesh(.05, .005, nx, ny, jitter=.2)
        net.add_sheet(f"layer{i}", nodes, triangles, t/RHO)
        net.add_terminal(f"rim{i}", f"layer{i}", left, rim=True)
        net.add_terminal(f"pad{i}", f"layer{i}", right)
        selections[f"layer{i}"] = left
    for i, length in enumerate(lengths):
        net.add_barrel(f"barrel{i}", f"rim{i}", f"rim{i+1}", length=length,
                       inner_radius=.15e-3, outer_radius=.175e-3, resistivity=RHO)
    # Independent expression (not the package barrel helper).
    barrel_r = RHO*np.array(lengths)/(np.pi*((.175e-3)**2-(.15e-3)**2))
    sheet_r = RHO*.05/(.005*np.array(thickness))
    return net, sheet_r, barrel_r, selections


def test_tied_strip_field_loss_and_named_results():
    net, nodes, _, left, right = strip()
    prepared = net.prepare(["supply"])
    r = RHO*.05/(.005*35e-6)
    result = prepared.solve({"supply": 3.3}, {"load": .4})
    np.testing.assert_allclose(result.sheet_voltages_v["copper"],
                               3.3-.4*r*nodes[:, 0]/.05, atol=1e-12, rtol=0)
    np.testing.assert_allclose(result.terminal_voltages_v["load"], 3.3-.4*r, atol=1e-12)
    assert result.terminal_currents_a["supply"] == pytest.approx(.4, rel=1e-6)
    assert result.terminal_currents_a["load"] == pytest.approx(-.4)
    assert result.sheet_losses_w["copper"] == pytest.approx(.4**2*r, rel=1e-6)
    assert result.branch_currents_a == result.branch_losses_w == {}
    assert result.checks["via_rims"] == {}
    mapping = prepared.node_map["copper"]
    np.testing.assert_array_equal(result.sheet_voltages_v["copper"], result.voltages_v[mapping])
    assert len(np.unique(mapping[left])) == len(np.unique(mapping[right])) == 1
    assert prepared.stats.geometric_nodes == len(nodes)
    assert prepared.stats.solver.nodes == len(nodes)-len(left)-len(right)+2
    assert all(value >= 0 for value in result.timings_s.values())
    assert result.passed


def test_two_voltage_sources_report_supplied_and_absorbed_current():
    net, *_ = strip()
    prepared = net.prepare(["load", "supply"])
    result = prepared.solve({"supply": 3.3, "load": 3.}, {})
    r = RHO*.05/(.005*35e-6)
    assert result.terminal_currents_a["supply"] == pytest.approx(.3/r, rel=1e-6)
    assert result.terminal_currents_a["load"] == pytest.approx(-.3/r, rel=1e-6)
    assert result.sheet_losses_w["copper"] == pytest.approx(.3**2/r, rel=1e-6)
    assert result.passed


def test_explicit_unequal_node_weights_leave_terminal_untied():
    # Unit square has four 0.5-S edge resistors; its diagonal is zero.
    nodes, triangles, left, right = rectangle_mesh(1., 1., 1, 1)
    net = Network()
    net.add_sheet("square", nodes, triangles, 1.)
    net.add_terminal("source", "square", left)
    net.add_terminal("weighted", "square", right, weights=[.25, .75])
    prepared = net.prepare(["source"])
    result = prepared.solve({"source": 0.}, {"weighted": 1.})
    np.testing.assert_allclose(result.terminal_voltages_v["weighted"], [-5/6, -7/6], atol=1e-12)
    assert len(np.unique(prepared.node_map["square"][right])) == 2
    assert result.sheet_losses_w["square"] == pytest.approx(13/12)
    assert result.terminal_currents_a["weighted"] == pytest.approx(-1)
    assert result.passed
    with pytest.raises(ValueError, match="equipotential"):
        prepared.transfer_resistance(["weighted"])
    with pytest.raises(ValueError, match="equipotential"):
        net.prepare(["weighted"])


@pytest.mark.parametrize("reverse", [False, True])
def test_lumped_series_part_between_sheets(reverse):
    net = Network()
    for name in ("a", "b"):
        nodes, triangles, left, right = rectangle_mesh(.05, .005, 8, 3)
        net.add_sheet(name, nodes, triangles, 35e-6/RHO)
        net.add_terminal(f"{name}:left", name, left)
        net.add_terminal(f"{name}:right", name, right)
    a, b = "a:right", "b:left"
    endpoints = (b, a) if reverse else (a, b)
    net.add_resistor("series", *endpoints, .01)
    result = net.prepare(["a:left"]).solve({"a:left": 12.}, {"b:right": .2})
    sheet_r = RHO*.05/(.005*35e-6)
    drop = 12.-result.terminal_voltages_v["b:right"][0]
    assert drop == pytest.approx(.2*(2*sheet_r+.01), rel=1e-6)
    assert result.branch_currents_a["series"] == pytest.approx(-.2 if reverse else .2, rel=1e-6)
    assert result.branch_losses_w["series"] == pytest.approx(.2**2*.01, rel=1e-6)
    assert result.passed


@pytest.mark.parametrize("nx,ny", [(8, 3), (16, 6)])
def test_branching_three_layer_via_chain(nx, ny):
    net, sheets, barrels, selections = branching(nx, ny)
    prepared = net.prepare(["pad0"])
    mapping = prepared.node_map
    rims = [mapping[name][indices[0]] for name, indices in selections.items()]
    assert len(set(rims)) == 3
    expected_drop = np.array([.3*(sheets[0]+barrels[0])+.1*sheets[1],
                              .3*(sheets[0]+barrels[0])+.2*(barrels[1]+sheets[2])])
    for scale in (1., 2.):
        result = prepared.solve({"pad0": 3.3}, {"pad1": .1*scale, "pad2": .2*scale})
        drop = [3.3-result.terminal_voltages_v[f"pad{i}"][0] for i in (1, 2)]
        np.testing.assert_allclose(drop, scale*expected_drop, rtol=1e-6, atol=0)
        np.testing.assert_allclose(list(result.branch_currents_a.values()), scale*np.array([.3, .2]), rtol=1e-6)
        np.testing.assert_allclose(list(result.sheet_losses_w.values()),
                                   scale**2*np.array([.3, .1, .2])**2*sheets, rtol=1e-6)
        np.testing.assert_allclose(list(result.branch_losses_w.values()),
                                   scale**2*np.array([.3, .2])**2*barrels, rtol=1e-6)
        assert set(result.checks["via_rims"]) == {"rim0", "rim1", "rim2"}
        assert result.passed, result.checks
    shared = sheets[0]+barrels[0]
    expected_z = [[shared+sheets[1], shared], [shared, shared+barrels[1]+sheets[2]]]
    np.testing.assert_allclose(prepared.transfer_resistance(["pad1", "pad2"], batch_size=1),
                               expected_z, rtol=1e-6)
    assert prepared.last_solve.rhs_columns == 2
    assert prepared.last_solve.max_batch_columns == 1


def test_external_source_and_sink_directly_on_rims_are_included_in_kcl():
    net = Network()
    for i in (0, 1):
        nodes, triangles, _, _ = rectangle_mesh(1., 1., 1, 1)
        net.add_sheet(str(i), nodes, triangles, 1.)
        net.add_terminal(f"rim{i}", str(i), np.arange(4), rim=True)
    net.add_barrel("barrel", "rim0", "rim1", length=.001,
                   inner_radius=.00015, outer_radius=.000175, resistivity=RHO)
    result = net.prepare(["rim0"]).solve({"rim0": 3.3}, {"rim1": .1})
    assert result.branch_currents_a["barrel"] == pytest.approx(.1, rel=1e-6)
    assert result.passed, result.checks


def test_missing_barrel_heat_fails_independent_power_check(monkeypatch):
    net, _, _, _ = branching()
    prepared = net.prepare(["pad0"])
    monkeypatch.setattr(core, "resistor_losses", lambda *args: np.zeros(2))
    result = prepared.solve({"pad0": 3.3}, {"pad1": .1, "pad2": .2})
    assert result.checks["current_balance"]["passed"]
    assert all(row["passed"] for row in result.checks["via_rims"].values())
    assert not result.checks["power_balance"]["passed"]
    assert not result.passed


def test_snapshot_isolation_and_factorization_reuse(monkeypatch):
    calls = []
    original = solver.splu
    def counted(matrix):
        calls.append(matrix.shape)
        return original(matrix)
    monkeypatch.setattr(solver, "splu", counted)
    net, nodes, triangles, _, _ = strip()
    prepared = net.prepare(["supply"])
    reference = prepared.solve({"supply": 3.3}, {"load": .4})
    reference.voltages_v[:] = -999
    reference.sheet_voltages_v["copper"][:] = -999
    prepared.node_map["copper"][:] = 0
    nodes[:] = -999
    triangles[:] = 0
    net.add_sheet("later", [[0., 0.], [1., 0.], [0., 1.]], [[0, 1, 2]], 1.)
    for source in (3.3, 12., -48.):
        result = prepared.solve({"supply": source}, {"load": .4})
        assert result.passed
        assert set(result.sheet_voltages_v) == {"copper"}
    z = prepared.transfer_resistance(["load"])
    assert z[0, 0] == pytest.approx(RHO*.05/(.005*35e-6), rel=1e-6)
    assert len(calls) == 1


@pytest.mark.parametrize("sources", [[], ["absent"], ["supply", "supply"], "supply"])
def test_invalid_sources(sources):
    net, *_ = strip()
    with pytest.raises(ValueError):
        net.prepare(sources)


@pytest.mark.parametrize("source,loads", [
    ({}, {}), ({"supply": 1., "load": 1.}, {}), ({"supply": np.nan}, {}),
    ({"supply": [3.3]}, {}), ({"supply": 3.3}, {"absent": .1}),
    ({"supply": 3.3}, {"supply": .1}), ({"supply": 3.3}, {"load": -.1}),
    ({"supply": 3.3}, {"load": np.inf}), ({"supply": 3.3}, []),
])
def test_invalid_load_cases(source, loads):
    net, *_ = strip()
    prepared = net.prepare(["supply"])
    with pytest.raises(ValueError):
        prepared.solve(source, loads)


@pytest.mark.parametrize("weights", [[1.], [0., 0.], [-1., 2.], [np.nan, 1.], [1., 1.]])
def test_invalid_current_allocation(weights):
    net = Network()
    nodes, triangles, left, _ = rectangle_mesh(1., 1., 1, 1)
    net.add_sheet("sheet", nodes, triangles, 1.)
    with pytest.raises(ValueError, match="weights"):
        net.add_terminal("load", "sheet", left, weights=weights)


def test_invalid_composition_and_no_partial_terminal_addition():
    net, _, _, left, _ = strip()
    with pytest.raises(ValueError, match="disjoint"):
        net.add_terminal("alias", "copper", left)
    with pytest.raises(ValueError, match="unique"):
        net.add_sheet("copper", [[0, 0], [1, 0], [0, 1]], [[0, 1, 2]], 1.)
    with pytest.raises(ValueError, match="unknown sheet"):
        net.add_terminal("new", "absent", [0])
    with pytest.raises(ValueError, match="integer"):
        net.add_terminal("new", "copper", [.5])
    with pytest.raises(ValueError, match="positive"):
        net.add_resistor("bad", "supply", "load", -1.)
    with pytest.raises(ValueError, match="distinct"):
        net.add_resistor("bad", "supply", "supply", 1.)
    assert net.prepare(["supply"]).solve({"supply": 3.3}, {"load": .1}).passed


def test_floating_sheet_is_not_silently_dropped():
    net, *_ = strip()
    net.add_sheet("floating", [[0, 0], [1, 0], [0, 1]], [[0, 1, 2]], 1.)
    with pytest.raises(ValueError, match="unanchored"):
        net.prepare(["supply"])


def test_invalid_barrel_and_unconnected_rim():
    net, *_ = strip()
    with pytest.raises(ValueError, match="rim terminals"):
        net.add_barrel("bad", "supply", "load", length=.001,
                       inner_radius=.00015, outer_radius=.000175, resistivity=RHO)
    net = Network()
    net.add_sheet("sheet", [[0, 0], [1, 0], [0, 1]], [[0, 1, 2]], 1.)
    net.add_terminal("source", "sheet", [0])
    net.add_terminal("rim", "sheet", [1, 2], rim=True)
    with pytest.raises(ValueError, match="incident branch"):
        net.prepare(["source"])


@pytest.mark.skipif(shutil.which("gmsh") is None, reason="external Gmsh not installed")
def test_annular_via_composition_accuracy_and_mesh_convergence(tmp_path):
    errors, sizes = [], []
    ri, ro, length = .15e-3, .175e-3, 1.6e-3
    radii, thicknesses = [1.5e-3, 1.e-3], [35e-6, 18e-6]
    expected = sum(RHO*np.log(b/ro)/(2*np.pi*t) for b, t in zip(radii, thicknesses))
    expected += RHO*length/(np.pi*(ro**2-ri**2))
    for level, h in enumerate((.1e-3, .05e-3)):
        # Function scope releases both prepared geometry and factors between levels.
        def run_level():
            net = Network()
            for i, (radius, t) in enumerate(zip(radii, thicknesses)):
                geo = tmp_path / f"level{level}_layer{i}.geo"
                write_annulus_geo(geo, ro, radius, h)
                run_gmsh(geo, geo.with_suffix(".msh"))
                mesh = read_sheet_mesh(geo.with_suffix(".msh"), boundaries=("inner", "outer"))
                net.add_sheet(str(i), mesh.nodes, mesh.triangles, t/RHO)
                net.add_terminal(f"rim{i}", str(i), mesh.boundaries["inner"], rim=True)
                net.add_terminal(f"pad{i}", str(i), mesh.boundaries["outer"])
            net.add_barrel("via", "rim0", "rim1", length=length, inner_radius=ri,
                           outer_radius=ro, resistivity=RHO)
            prepared = net.prepare(["pad0"])
            result = prepared.solve({"pad0": 3.3}, {"pad1": .25})
            measured = (3.3-result.terminal_voltages_v["pad1"][0])/.25
            assert result.passed, result.checks
            return abs(measured-expected)/expected, prepared.stats.geometric_nodes
        error, size = run_level()
        errors.append(error)
        sizes.append(size)
    assert max(errors) <= .005
    assert errors[1] < errors[0]
    assert sizes[1] > sizes[0]
    print(f"composed annular via: errors={errors}, geometric_nodes={sizes}")

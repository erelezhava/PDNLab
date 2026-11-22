"""One net of a real board as a network (board_network.build_net_network).

The composition is checked against an exact answer: a through-hole pin contacted on
its component's side (F.Cu), its barrel to B.Cu, and a copper ring on B.Cu out to a
ring-shaped SMD pad; the resistance between the two pins is the barrel formula plus
ln(b/a) / (2 pi sigma t) of the ring. Everything else uses the public thermal board.
Needs a gmsh executable (skipped without one).
"""

import math
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon, box

from pdnlab_dc.board import CopperFeature, DrillFeature, PinPad
from pdnlab_dc.board_mesh import mesh_net
from pdnlab_dc.board_network import (COPPER_RESISTIVITY_OHM_M, IslandError, build_net_network,
                                     net_pins)
from pdnlab_dc.copper_cleanup import clean_copper
from pdnlab_dc.ipc2581_board import read_ipc2581_board
from pdnlab_dc.vias import barrel_resistance

pytestmark = pytest.mark.skipif(shutil.which("gmsh") is None, reason="external Gmsh not installed")

FIXTURE = Path(__file__).parent / "fixtures" / "thermal_relief" / "thermal_relief.xml"
PLATING = 25e-6


@pytest.fixture(scope="module")
def thermal():
    board = read_ipc2581_board(FIXTURE)
    clean = clean_copper(board, PLATING)
    return board, clean, mesh_net(clean, board, "GND")


def drop(net, source, sink, amps=1.0):
    prepared = net.network.prepare([net.contacts[source].terminal])
    result = prepared.solve({net.contacts[source].terminal: 1.0},
                            {net.contacts[sink].terminal: amps})
    assert result.passed
    v = result.terminal_voltages_v
    return (v[net.contacts[source].terminal][0] - v[net.contacts[sink].terminal][0]) / amps, result


def test_pin_barrel_ring_against_the_exact_answer(thermal):
    base = thermal[0]
    cx, cy, d, b = 0.2, 0.1, 1.0e-3, 2.0e-3
    a = d / 2 + PLATING
    inner = Point(cx, cy).buffer(b, quad_segs=256)
    ring = Polygon(Point(cx, cy).buffer(b + 1e-3, quad_segs=256).exterior.coords,
                   [inner.exterior.coords])                 # shares the inner circle exactly
    features = (
        CopperFeature("pad:F.Cu:U1.1", "GND", "F.Cu", "pad", Point(cx, cy).buffer(1.5e-3, quad_segs=256),
                      drill_id="drill:P"),
        CopperFeature("pad:B.Cu:U1.1", "GND", "B.Cu", "pad", inner, drill_id="drill:P"),
        CopperFeature("pad:B.Cu:U2.1", "GND", "B.Cu", "pad", ring))
    pads = (PinPad("pin:U1.1", "U1", "1", "GND", (cx, cy), ("pad:F.Cu:U1.1", "pad:B.Cu:U1.1"),
                   "drill:P"),
            PinPad("pin:U2.1", "U2", "1", "GND", (cx, cy), ("pad:B.Cu:U2.1",), None))
    drill = DrillFeature("drill:P", (cx, cy), d, "plated", "F.Cu", "B.Cu", "pin")
    # B.Cu made 70 µm (the thermal board's layers are all 35 µm), so that a wrong
    # thickness would show.
    layers = tuple(replace(L, thickness_m=70e-6, z_m=L.z_m + 17.5e-6) if L.id == "B.Cu" else L
                   for L in base.layers)
    board = replace(base, layers=layers, features=features, pads=pads, drills=(drill,),
                    component_layers=(("U1", "F.Cu"), ("U2", "B.Cu")))
    clean = clean_copper(board, PLATING)
    mesh = mesh_net(clean, board, "GND")
    layers = {layer.id: layer for layer in board.layers}
    length = layers["B.Cu"].z_m - layers["F.Cu"].z_m
    sheet = layers["B.Cu"].thickness_m / COPPER_RESISTIVITY_OHM_M
    ring_r = math.log(b / a) / (2 * math.pi * sheet)
    # Measured 2026-11-22: 0.435639 against 0.435720 mOhm exact (-0.019 %) at 35 µm.
    for barrel_rho in (None, 3 * COPPER_RESISTIVITY_OHM_M):     # the solved value must follow
        net = build_net_network(board, clean, mesh, ["U1.1", "U2.1"], barrel_resistivity=barrel_rho)
        (barrel,) = net.barrels
        expected_barrel = barrel_resistance(length, d / 2, a, barrel_rho or COPPER_RESISTIVITY_OHM_M)
        assert barrel.resistance_ohm == pytest.approx(expected_barrel, rel=1e-12)
        r, _ = drop(net, "U1.1", "U2.1")
        assert r == pytest.approx(expected_barrel + ring_r, rel=0.005), r / (expected_barrel + ring_r)
    # U1.1's hole joins through its F.Cu pad; on B.Cu it has its own rim.
    assert net.contacts["U1.1"].layer_id == "F.Cu" and net.contacts["U2.1"].layer_id == "B.Cu"
    assert set(net.network._terminals) == {"pin:U1.1", "pin:U2.1", "rim:drill:P:B.Cu"}


def test_thermal_board_net_structure(thermal):
    board, clean, mesh = thermal
    assert net_pins(board, "GND") == ["R1.2", "TP1.1", "TP2.1"]
    net = build_net_network(board, clean, mesh, ["TP2.1", "TP1.1"])
    layers = {layer.id: layer for layer in board.layers}
    for layer_id, g in net.sheet_conductance_s.items():
        assert g == pytest.approx(layers[layer_id].thickness_m / COPPER_RESISTIVITY_OHM_M)
    # Three barrels per plated hole: F.Cu-In1, In1-In2, In2-B.Cu, lengths from the stackup.
    assert sorted(x.drill_id for x in net.barrels) == ["drill:H1"] * 3 + ["drill:H2"] * 3 + ["drill:H3"] * 3
    drills = {x.id: x for x in board.drills}
    for x in net.barrels:
        dr = drills[x.drill_id]
        length = layers[x.lower_layer_id].z_m - layers[x.upper_layer_id].z_m
        assert x.resistance_ohm == pytest.approx(barrel_resistance(
            length, dr.diameter_m / 2, dr.diameter_m / 2 + PLATING, COPPER_RESISTIVITY_OHM_M))
    # TP1 is through-hole, mounted on F.Cu: contact on its F.Cu pad, which takes H3's rim.
    assert net.contacts["TP1.1"].layer_id == "F.Cu"
    assert "rim:drill:H3:F.Cu" not in net.network._terminals
    assert "rim:drill:H3:B.Cu" in net.network._terminals
    r, result = drop(net, "TP2.1", "TP1.1")
    assert 0 < r < 1e-3
    # All of the current leaves at TP1; none at holes or other pins.
    assert result.terminal_currents_a["pin:TP1.1"] == pytest.approx(-1.0)


def test_resistivity_defaults_and_overrides(thermal):
    board, clean, mesh = thermal
    base = build_net_network(board, clean, mesh, ["TP2.1", "TP1.1"])
    assert base.resistivity_ohm_m == base.barrel_resistivity_ohm_m == 1.724e-8
    worse = build_net_network(board, clean, mesh, ["TP2.1", "TP1.1"], barrel_resistivity=2e-8)
    assert worse.sheet_conductance_s == base.sheet_conductance_s
    assert all(w.resistance_ohm == pytest.approx(b.resistance_ohm * 2e-8 / 1.724e-8)
               for w, b in zip(worse.barrels, base.barrels))
    hot = build_net_network(board, clean, mesh, ["TP2.1", "TP1.1"], resistivity=2e-8)
    assert hot.barrel_resistivity_ohm_m == 2e-8
    r0, _ = drop(base, "TP2.1", "TP1.1")
    r1, _ = drop(hot, "TP2.1", "TP1.1")
    assert r1 == pytest.approx(r0 * 2e-8 / 1.724e-8, rel=1e-9)     # everything scales with rho
    assert "1.724e-08" in base.assumptions[0]


def test_island_on_the_net_is_an_error(thermal):
    board, _, _ = thermal
    stray = CopperFeature("test:stray", "GND", "B.Cu", "fill", box(0.190, 0.090, 0.192, 0.092))
    broken = replace(board, features=board.features + (stray,))
    clean = clean_copper(broken, PLATING)
    with pytest.raises(IslandError, match=r"net 'GND': 1 island\(s\) not joined .*nothing "
                                          r"simulated: 1 piece\(s\) on B\.Cu, 4\.0000 mm², at B\.Cu"):
        build_net_network(broken, clean, mesh_net(clean, broken, "GND"), ["TP2.1"])


def test_pin_rejections(thermal):
    board, clean, mesh = thermal
    with pytest.raises(ValueError, match="pin 'U9.1': no such pin"):
        build_net_network(board, clean, mesh, ["U9.1"])
    with pytest.raises(ValueError, match=r"pin 'R1.1' is on net '\+3V3', not 'GND'"):
        build_net_network(board, clean, mesh, ["R1.1"])
    unknown_side = replace(board, component_layers=())
    with pytest.raises(ValueError, match="pin 'TP1.1': the side its component is mounted on"):
        build_net_network(unknown_side, clean, mesh, ["TP1.1"])
    for kwargs in ({"resistivity": 0}, {"resistivity": True}, {"barrel_resistivity": -1}):
        with pytest.raises(ValueError, match="resistivity must be a positive number"):
            build_net_network(board, clean, mesh, ["TP2.1"], **kwargs)


def test_component_sides_are_read_from_the_export():
    board = read_ipc2581_board(FIXTURE)
    assert board.component_layers == (("R1", "F.Cu"), ("TP1", "F.Cu"), ("TP2", "F.Cu"))
    pads_board = read_ipc2581_board(FIXTURE.parent.parent / "pad_shapes" / "pad_shapes.xml")
    assert {ref for ref, side in pads_board.component_layers if side == "B.Cu"} == {"JP2", "R2", "R4"}
    with pytest.raises(ValueError, match="component_layers lacks pin components"):
        replace(board, component_layers=(("R1", "F.Cu"),))
    with pytest.raises(ValueError, match="outer copper layer"):
        replace(board, component_layers=(("R1", "In1.Cu"), ("TP1", "F.Cu"), ("TP2", "F.Cu")))


def test_slot_barrel_against_the_exact_answer(thermal):
    # A plated slot 4 x 1 mm, its long axis at 30 degrees, through the board. J1's pad on
    # F.Cu holds it (the pin's contact, on its component's side); on B.Cu the slot passes
    # through J2's square SMD pad, so its B.Cu edge lies in J2's contact. Both ends
    # are equipotential: the resistance between the pins is the slot barrel alone, the
    # round barrel's ring halves joined by two flat sides of plating. 4 mm is long enough
    # that the hole is larger than pi * d^2, the round-hole bound the mesh used to apply.
    base = thermal[0]
    cx, cy, d, length, angle = 0.2, 0.1, 1.0e-3, 4.0e-3, 30.0
    features = (
        CopperFeature("pad:F.Cu:J1.1", "GND", "F.Cu", "pad", Point(cx, cy).buffer(4e-3, quad_segs=64),
                      drill_id="drill:SLOT1"),
        CopperFeature("pad:B.Cu:J2.1", "GND", "B.Cu", "pad", box(cx - 4e-3, cy - 4e-3, cx + 4e-3, cy + 4e-3)))
    pads = (PinPad("pin:J1.1", "J1", "1", "GND", (cx, cy), ("pad:F.Cu:J1.1",), "drill:SLOT1"),
            PinPad("pin:J2.1", "J2", "1", "GND", (cx, cy), ("pad:B.Cu:J2.1",), None))
    slot = DrillFeature("drill:SLOT1", (cx, cy), d, "plated", "F.Cu", "B.Cu", "pin",
                        slot_length_m=length, slot_angle_deg=angle)
    board = replace(base, features=features, pads=pads, drills=(slot,),
                    component_layers=(("J1", "F.Cu"), ("J2", "B.Cu")))
    clean = clean_copper(board, PLATING)
    # The hole cut on F.Cu: a stadium of half-width d/2 + plating along the slot's axis.
    a = d / 2 + PLATING
    (piece,) = [p for p in clean.pieces if p.layer_id == "F.Cu"]
    (hole,) = [Polygon(r) for r in piece.geometry.interiors]
    exact = math.pi * a ** 2 + (length - d) * 2 * a      # polygon inside, 1 µm chord rule
    assert hole.area == pytest.approx(exact, rel=1e-3) and hole.area < exact
    along = (math.cos(math.radians(angle)), math.sin(math.radians(angle)))
    reach = length / 2 + PLATING - 1e-5
    assert hole.contains(Point(cx + reach * along[0], cy + reach * along[1]))
    assert not hole.contains(Point(cx - reach * along[1], cy + reach * along[0]))   # across
    mesh = mesh_net(clean, board, "GND")
    net = build_net_network(board, clean, mesh, ["J1.1", "J2.1"])
    (barrel,) = net.barrels
    layers = {layer.id: layer for layer in board.layers}
    barrel_length = layers["B.Cu"].z_m - layers["F.Cu"].z_m
    wall = math.pi * (a ** 2 - (d / 2) ** 2) + 2 * (length - d) * PLATING
    expected = COPPER_RESISTIVITY_OHM_M * barrel_length / wall
    assert barrel.resistance_ohm == pytest.approx(expected, rel=1e-12)
    assert expected < 0.6 * barrel_resistance(barrel_length, d / 2, a, COPPER_RESISTIVITY_OHM_M)
    r, _ = drop(net, "J1.1", "J2.1")
    assert r == pytest.approx(expected, rel=1e-6)
    assert set(net.network._terminals) == {"pin:J1.1", "pin:J2.1"}

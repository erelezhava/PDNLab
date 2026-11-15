"""Per-via current output of the net prototype, against cases with a known answer.

Main case: two strips on different layers joined by one via. Every ampere must pass through
that via, and the terminal-to-terminal resistance has a closed form (below). Further cases:
a three-layer via with two loads (currents fixed by topology), an ideal through-hole lead,
and inputs the model does not support, which must raise.

Needs a gmsh executable (skipped without one, like the other Gmsh tests).
"""

import importlib.util
import math
from pathlib import Path
import shutil

import pytest

if shutil.which("gmsh") is None:
    pytest.skip("gmsh executable required", allow_module_level=True)

SCRIPT = Path(__file__).resolve().parents[1] / "experiments" / "exp08_real_net.py"
spec = importlib.util.spec_from_file_location("exp08_real_net", SCRIPT)
exp08 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exp08)

RHO = 1.724e-8
PLATING = 0.025  # mm

# Two strips, one via at (X0, Y0). All lengths in mm.
# F.Cu strip: width W1, runs from E1 behind the via to a full-width pad P1 whose inner edge is
# L1 ahead of the via. B.Cu strip: width W2, runs the other way to pad P2 at L2, dead end E2.
W1, W2, E1, E2, L1, L2 = 3.0, 4.0, 2.0, 1.5, 6.0, 8.0
T1, T2, DIELECTRIC = 0.035, 0.018, 1.5
DRILL, VIA_PAD = 0.3, 0.6
X0, Y0 = 20.0, 10.0
R_OUT = DRILL / 2 + PLATING


def _zone(layer, x0, x1, width):
    return (f'(zone (net "PWR") (layer "{layer}") (filled_polygon (layer "{layer}") (pts '
            f"(xy {x0} {Y0 - width / 2}) (xy {x1} {Y0 - width / 2}) "
            f"(xy {x1} {Y0 + width / 2}) (xy {x0} {Y0 + width / 2}))))")


def _pad(ref, layer, x, width, kind="smd rect", extra=""):
    return (f'(footprint "f" (at 0 0) (property "Reference" "{ref}")\n'
            f'  (pad "1" {kind} (at {x} {Y0}) (size 1 {width}) {extra}(layers "{layer}") (net "PWR") '
            f'(pintype "passive")))')


TWO_STRIPS = f'''(kicad_pcb
 (layers (0 "F.Cu" signal) (2 "B.Cu" signal))
 (setup (stackup
  (layer "F.Cu" (type "copper") (thickness {T1}))
  (layer "dielectric 1" (type "core") (thickness {DIELECTRIC}))
  (layer "B.Cu" (type "copper") (thickness {T2}))))
 {_pad("P1", "F.Cu", X0 + L1 + 0.5, W1)}
 {_pad("P2", "B.Cu", X0 - L2 - 0.5, W2)}
 (via (at {X0} {Y0}) (size {VIA_PAD}) (drill {DRILL}) (layers "F.Cu" "B.Cu") (net "PWR"))
 {_zone("F.Cu", X0 - E1, X0 + L1 + 1, W1)}
 {_zone("B.Cu", X0 - L2 - 1, X0 + E2, W2)})'''


def strip_side(length, width, dead_end, thickness_mm, radius):
    """Resistance from an equipotential circular hole to a far cross-section of a strip, ohms.

    Strip of width W with insulating edges, closed `dead_end` behind the hole centre, all
    current leaving through a cross-section `length` ahead of it. The method of images gives
    the potential of a line source in that strip, -(I / 2 pi g) * (ln|sinh(pi z / W)| +
    ln|sinh(pi (z + 2e) / W)|). Making the circle an equipotential adds a dipole whose image
    lowers the rim potential by (pi r / W)^2 coth^2(2 pi e / W). The result,

        R = L / (g W) + [2 pi e / W - ln(4 pi r / W * sinh(2 pi e / W))
                         - (pi r / W)^2 coth^2(2 pi e / W)] / (2 pi g),

    is exact up to terms of order (pi r / W)^4 and exp(-2 pi L / W). Here that is below
    2e-4 of the bracket, so far below the mesh tolerances used in the tests.
    The second return value is the first-order peak-to-average current density round the rim.
    """
    g = thickness_mm * 1e-3 / RHO
    a = math.pi / width / math.tanh(2 * math.pi * dead_end / width)
    spread = (2 * math.pi * dead_end / width
              - math.log(4 * math.pi * radius / width * math.sinh(2 * math.pi * dead_end / width))
              - (a * radius) ** 2)
    return length / (g * width) + spread / (2 * math.pi * g), 1 + 2 * a * radius


PLATING_AREA = math.pi * (R_OUT ** 2 - (DRILL / 2) ** 2)  # mm2
R_BARREL = RHO * (T1 / 2 + DIELECTRIC + T2 / 2) * 1e-3 / (PLATING_AREA * 1e-6)
R_SIDE_1, CROWD_1 = strip_side(L1, W1, E1, T1, R_OUT)
R_SIDE_2, CROWD_2 = strip_side(L2, W2, E2, T2, R_OUT)
R_TOTAL = R_SIDE_1 + R_BARREL + R_SIDE_2


def solve(tmp_path, text, source, loads, h=0.5, sides=None, growth=None):
    path = tmp_path / "board.kicad_pcb"
    path.write_text(text)
    board = exp08.read_net(path, "PWR")
    layers = exp08.build_layers(board, PLATING, rim_segments=sides)
    meshes = {n: exp08.mesh_layer(n, layers[n], board["pads"], tmp_path, h, "gmsh", growth=growth)
              for n in board["copper"] if layers[n]["polys"]}
    model = exp08.assemble(board, layers, meshes, RHO, PLATING)
    ids, Z, unreachable, ctx = exp08.transfer_matrix(model, [source], list(loads))
    assert ids == list(loads) and not unreachable
    drop, balance = exp08.solve_scenario(model, ctx, loads)
    return dict(board=board, model=model, ctx=ctx, drop=drop, Z=Z, balance=balance)


def test_closed_form_has_the_expected_size():
    # Guards the test itself: the hole terms must be large enough for the tolerances to see them.
    assert R_BARREL / R_TOTAL == pytest.approx(0.245, abs=0.005)
    assert (R_TOTAL - R_BARREL - L1 * RHO / (T1 * 1e-3 * W1) - L2 * RHO / (T2 * 1e-3 * W2)) / R_TOTAL > 0.06


# The finite-element resistance approaches the closed form from below as the mesh is refined.
@pytest.mark.parametrize("h,sides,growth,rel", [
    (0.5, None, None, 3e-3),    # original mesh, no size field
    (0.5, 48, 0.2, 1.5e-3),     # size field at the via hole
    (0.25, 96, 0.15, 6e-4),
])
def test_one_via_joining_two_strips(tmp_path, h, sides, growth, rel):
    s = solve(tmp_path, TWO_STRIPS, "P1.1", {"P2.1": 1.0}, h, sides, growth)
    assert s["Z"][0, 0] == pytest.approx(R_TOTAL, rel=rel)
    assert s["Z"][0, 0] < R_TOTAL

    vias = exp08.via_currents(s["board"], s["model"], s["drop"])
    assert len(vias) == 1
    v = vias[0]
    assert v["layers"] == ["F.Cu", "B.Cu"]
    # All of the 1 A crosses this via: in from F.Cu, down the barrel, out to B.Cu.
    assert [b["amp"] for b in v["barrel"]] == pytest.approx([1.0], abs=1e-9)
    assert v["rim"]["F.Cu"] == pytest.approx(1.0, abs=1e-9)
    assert v["rim"]["B.Cu"] == pytest.approx(-1.0, abs=1e-9)
    assert v["kcl_residual"] < 1e-9
    assert v["barrel"][0]["ohm"] == pytest.approx(R_BARREL, rel=1e-12)
    # Densities, A/mm2. The hole is a polygon inscribed in the circle, hence the 0.5 %.
    assert v["plating_area_mm2"] == pytest.approx(PLATING_AREA, rel=1e-12)
    assert v["j_barrel"] == pytest.approx(1.0 / PLATING_AREA, rel=1e-9)
    assert v["j_edge_avg"]["F.Cu"] == pytest.approx(1.0 / (2 * math.pi * R_OUT * T1), rel=5e-3)
    assert v["j_edge_avg"]["B.Cu"] == pytest.approx(1.0 / (2 * math.pi * R_OUT * T2), rel=5e-3)
    # Crowding towards the side the current comes from; the formula is first order in r / W.
    assert v["j_edge_peak"]["F.Cu"] / v["j_edge_avg"]["F.Cu"] == pytest.approx(CROWD_1, rel=0.04)
    assert v["j_edge_peak"]["B.Cu"] / v["j_edge_avg"]["B.Cu"] == pytest.approx(CROWD_2, rel=0.04)

    check = exp08.via_conservation(s["model"], s["ctx"], s["drop"], vias)
    assert check["ok"]
    assert check["source_current_A"] == pytest.approx(1.0, abs=1e-9)
    assert [g["gap"] for g in check["gaps"]] == ["F.Cu|B.Cu"]
    assert check["gaps"][0]["barrels_down_A"] == pytest.approx(1.0, abs=1e-9)
    assert check["gaps"][0]["solver_terminals_above_A"] == pytest.approx(1.0, abs=1e-9)
    assert check["ideal_leads"] == []


def test_hole_refinement_reduces_the_error(tmp_path):
    coarse = solve(tmp_path, TWO_STRIPS, "P1.1", {"P2.1": 1.0}, 0.5)
    fine = solve(tmp_path, TWO_STRIPS, "P1.1", {"P2.1": 1.0}, 0.5, 96, 0.15)
    assert abs(fine["Z"][0, 0] / R_TOTAL - 1) < 0.5 * abs(coarse["Z"][0, 0] / R_TOTAL - 1)
    assert len(fine["model"]["xy"]) > len(coarse["model"]["xy"])


def test_conservation_check_fails_on_a_wrong_barrel_current(tmp_path):
    s = solve(tmp_path, TWO_STRIPS, "P1.1", {"P2.1": 1.0}, 0.5, 48, 0.2)
    vias = exp08.via_currents(s["board"], s["model"], s["drop"])
    assert exp08.via_conservation(s["model"], s["ctx"], s["drop"], vias)["ok"]

    # A barrel resistance that differs from the one in the solved matrix by 0.01 %.
    s["model"]["segments"][0]["ohm"] *= 1.0001
    wrong = exp08.via_currents(s["board"], s["model"], s["drop"])
    check = exp08.via_conservation(s["model"], s["ctx"], s["drop"], wrong)
    assert not check["ok"]
    assert check["max_rim_kcl_residual_A"] == pytest.approx(1e-4, rel=0.01)
    assert abs(check["gaps"][0]["difference_A"]) == pytest.approx(1e-4, rel=0.01)

    # A via missing from the list: the gap total no longer matches the solver.
    assert not exp08.via_conservation(s["model"], s["ctx"], s["drop"], [])["ok"]


THREE_LAYERS = f'''(kicad_pcb
 (layers (0 "F.Cu" signal) (1 "In1.Cu" signal) (2 "B.Cu" signal))
 (setup (stackup
  (layer "F.Cu" (type "copper") (thickness 0.035))
  (layer "dielectric 1" (type "prepreg") (thickness 0.2))
  (layer "In1.Cu" (type "copper") (thickness 0.0152))
  (layer "dielectric 2" (type "core") (thickness 1.2))
  (layer "B.Cu" (type "copper") (thickness 0.035))))
 {_pad("P1", "F.Cu", X0 + 6.5, 3)}
 {_pad("P2", "In1.Cu", X0 - 6.5, 3)}
 {_pad("P3", "B.Cu", X0 + 6.5, 3)}
 (via (at {X0} {Y0}) (size {VIA_PAD}) (drill {DRILL}) (layers "F.Cu" "B.Cu") (net "PWR"))
 {_zone("F.Cu", X0 - 2, X0 + 7, 3)}
 {_zone("In1.Cu", X0 - 7, X0 + 2, 3)}
 {_zone("B.Cu", X0 - 2, X0 + 7, 3)})'''


def test_three_layer_via_splits_current_between_two_loads(tmp_path):
    s = solve(tmp_path, THREE_LAYERS, "P1.1", {"P2.1": 0.3, "P3.1": 0.7}, 0.5, 48, 0.2)
    v, = exp08.via_currents(s["board"], s["model"], s["drop"])
    assert v["layers"] == ["F.Cu", "In1.Cu", "B.Cu"]
    assert [(b["upper"], b["lower"]) for b in v["barrel"]] == [("F.Cu", "In1.Cu"), ("In1.Cu", "B.Cu")]
    assert [b["amp"] for b in v["barrel"]] == pytest.approx([1.0, 0.7], abs=1e-9)
    assert [v["rim"][n] for n in v["layers"]] == pytest.approx([1.0, -0.3, -0.7], abs=1e-9)
    assert v["barrel_abs_max"] == pytest.approx(1.0, abs=1e-9)
    check = exp08.via_conservation(s["model"], s["ctx"], s["drop"], [v])
    assert check["ok"]
    assert [g["barrels_down_A"] for g in check["gaps"]] == pytest.approx([1.0, 0.7], abs=1e-9)
    assert [g["solver_terminals_above_A"] for g in check["gaps"]] == pytest.approx([1.0, 0.7], abs=1e-9)


# No via: a through-hole pin joins the layers as an ideal lead. It must not be counted as a
# barrel, and the current it passes between layers must be reported.
THROUGH_HOLE_LEAD = f'''(kicad_pcb
 (layers (0 "F.Cu" signal) (2 "B.Cu" signal))
 (setup (stackup
  (layer "F.Cu" (type "copper") (thickness {T1}))
  (layer "dielectric 1" (type "core") (thickness {DIELECTRIC}))
  (layer "B.Cu" (type "copper") (thickness {T2}))))
 {_pad("P1", "F.Cu", X0 + 6.5, 3)}
 {_pad("P2", "B.Cu", X0 - 6.5, 3)}
 (footprint "f" (at 0 0) (property "Reference" "J9")
  (pad "1" thru_hole circle (at {X0} {Y0}) (size 1.7 1.7) (drill 1.0) (layers "*.Cu") (net "PWR")
   (pintype "passive")))
 {_zone("F.Cu", X0 - 2, X0 + 7, 3)}
 {_zone("B.Cu", X0 - 7, X0 + 2, 3)})'''


def test_ideal_through_hole_lead_is_reported_and_is_not_a_barrel(tmp_path):
    s = solve(tmp_path, THROUGH_HOLE_LEAD, "P1.1", {"P2.1": 1.0}, 0.5)
    vias = exp08.via_currents(s["board"], s["model"], s["drop"])
    assert vias == []
    check = exp08.via_conservation(s["model"], s["ctx"], s["drop"], vias)
    assert check["ok"]
    assert check["gaps"][0]["barrels_down_A"] == 0.0
    lead, = check["ideal_leads"]
    assert lead["pins"] == ["J9.1"]
    assert lead["external"] == pytest.approx(0.0, abs=1e-9)
    # The lead takes 1 A out of the F.Cu copper and feeds it into B.Cu.
    assert lead["per_layer"]["F.Cu"] == pytest.approx(-1.0, abs=1e-9)
    assert lead["per_layer"]["B.Cu"] == pytest.approx(1.0, abs=1e-9)


def test_via_in_pad_is_rejected_not_reported(tmp_path):
    # P1 moved onto the via: the rim is then part of the pad equipotential.
    text = TWO_STRIPS.replace(f"(at {X0 + L1 + 0.5} {Y0}) (size 1 {W1})", f"(at {X0} {Y0}) (size 1 {W1})")
    assert text != TWO_STRIPS
    path = tmp_path / "board.kicad_pcb"
    path.write_text(text)
    board = exp08.read_net(path, "PWR")
    layers = exp08.build_layers(board, PLATING, rim_segments=48)
    meshes = {n: exp08.mesh_layer(n, layers[n], board["pads"], tmp_path, 0.5, "gmsh", growth=0.2)
              for n in board["copper"] if layers[n]["polys"]}
    model = exp08.assemble(board, layers, meshes, RHO, PLATING)
    ids, Z, unreachable, ctx = exp08.transfer_matrix(model, ["P1.1"], ["P2.1"])
    drop, _ = exp08.solve_scenario(model, ctx, {"P2.1": 1.0})
    with pytest.raises(NotImplementedError, match="via in pad"):
        exp08.via_currents(board, model, drop)


@pytest.mark.parametrize("sides", [12, 50, 24.5])
def test_bad_hole_side_count_is_rejected(tmp_path, sides):
    path = tmp_path / "board.kicad_pcb"
    path.write_text(TWO_STRIPS)
    with pytest.raises(ValueError, match="rim_segments"):
        exp08.build_layers(exp08.read_net(path, "PWR"), PLATING, rim_segments=sides)


def test_refinement_that_cannot_refine_is_rejected(tmp_path):
    path = tmp_path / "board.kicad_pcb"
    path.write_text(TWO_STRIPS)
    board = exp08.read_net(path, "PWR")
    layers = exp08.build_layers(board, PLATING, rim_segments=16)
    with pytest.raises(ValueError, match="nothing to refine"):  # hole side 0.069 mm >= h
        exp08.mesh_layer("F.Cu", layers["F.Cu"], board["pads"], tmp_path, 0.05, "gmsh", growth=0.2)
    with pytest.raises(ValueError, match="growth"):
        exp08.mesh_layer("F.Cu", layers["F.Cu"], board["pads"], tmp_path, 0.5, "gmsh", growth=0.0)

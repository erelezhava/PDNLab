"""Local mesh refinement at through-hole pads, against a case with a closed form.

A square through-hole pad feeds a strip through one narrow spoke, like one arm of a thermal
relief. Spoke and strip are straight, so the only part without a one-line answer is the
abrupt, centred width step where the spoke joins the strip. Its extra resistance is the
classic conformal-mapping result for a one-sided step of ratio a = W / w,

    (1 / pi) * [ (a^2 + 1) / a * ln((a + 1) / (a - 1)) - 2 * ln(4 a / (a^2 - 1)) ]  squares,

halved because the centred step is two mirrored one-sided steps in parallel. It holds when
both sections are longer than their own width (evanescent terms below 1e-5 here).

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
PLATING = 0.025   # mm
THICKNESS = 0.035  # mm
X0, Y0 = 20.0, 10.0
PAD, SPOKE_LENGTH, SPOKE_WIDTH, WIDTH, LENGTH = 1.7, 1.0, 0.5, 3.0, 5.0
STEP_X = X0 + PAD / 2 + SPOKE_LENGTH
OUTLINE = [(X0 + 0.6, Y0 - SPOKE_WIDTH / 2), (STEP_X, Y0 - SPOKE_WIDTH / 2), (STEP_X, Y0 - WIDTH / 2),
           (STEP_X + LENGTH + 1, Y0 - WIDTH / 2), (STEP_X + LENGTH + 1, Y0 + WIDTH / 2), (STEP_X, Y0 + WIDTH / 2),
           (STEP_X, Y0 + SPOKE_WIDTH / 2), (X0 + 0.6, Y0 + SPOKE_WIDTH / 2)]


def board_text(thermal_gap):
    fill = "" if thermal_gap is None else f"(fill yes (thermal_gap {thermal_gap}) (thermal_bridge_width {SPOKE_WIDTH}))"
    return f'''(kicad_pcb
 (layers (0 "F.Cu" signal) (2 "B.Cu" signal))
 (setup (stackup
  (layer "F.Cu" (type "copper") (thickness {THICKNESS}))
  (layer "dielectric 1" (type "core") (thickness 1.5))
  (layer "B.Cu" (type "copper") (thickness {THICKNESS}))))
 (footprint "f" (at 0 0) (property "Reference" "J9")
  (pad "1" thru_hole rect (at {X0} {Y0}) (size {PAD} {PAD}) (drill 1.0) (layers "*.Cu") (net "PWR")
   (pintype "passive")))
 (footprint "f" (at 0 0) (property "Reference" "P2")
  (pad "1" smd rect (at {STEP_X + LENGTH + 0.5} {Y0}) (size 1 {WIDTH}) (layers "F.Cu") (net "PWR")
   (pintype "passive")))
 (zone (net "PWR") (layer "F.Cu") {fill}
  (filled_polygon (layer "F.Cu") (pts {" ".join(f"(xy {x} {y})" for x, y in OUTLINE)}))))'''


def expected_resistance():
    a = WIDTH / SPOKE_WIDTH
    step = 0.5 / math.pi * ((a * a + 1) / a * math.log((a + 1) / (a - 1)) - 2 * math.log(4 * a / (a * a - 1)))
    return (SPOKE_LENGTH / SPOKE_WIDTH + LENGTH / WIDTH + step) * RHO / (THICKNESS * 1e-3), step


def solve(tmp_path, h, sides=None, growth=None, thermal_gap=SPOKE_LENGTH):
    path = tmp_path / "board.kicad_pcb"
    path.write_text(board_text(thermal_gap))
    board = exp08.read_net(path, "PWR")
    layers = exp08.build_layers(board, PLATING, rim_segments=sides)
    meshes = {n: exp08.mesh_layer(n, layers[n], board["pads"], tmp_path, h, "gmsh", growth=growth)
              for n in board["copper"] if layers[n]["polys"]}
    model = exp08.assemble(board, layers, meshes, RHO, PLATING)
    loads, Z, unreachable, _ = exp08.transfer_matrix(model, ["J9.1"], ["P2.1"])
    assert loads == ["P2.1"] and not unreachable
    return Z[0, 0], board, layers, meshes


def test_step_term_is_a_visible_part_of_the_answer():
    total, step = expected_resistance()
    assert step == pytest.approx(0.4503, abs=1e-4)
    assert step * RHO / (THICKNESS * 1e-3) / total > 0.10


def test_thermal_gap_and_fed_pads_are_read(tmp_path):
    _, board, layers, _ = solve(tmp_path, 0.5, 48, 0.2)
    assert board["relief"] == {"F.Cu": SPOKE_LENGTH}
    assert layers["F.Cu"]["relief_mm"] == SPOKE_LENGTH
    fed, = layers["F.Cu"]["fed_pads"]
    assert fed["pin"] == "J9.1"
    assert fed["reach"] == pytest.approx(PAD / 2 * math.sqrt(2))
    assert fed["hole_radius"] == pytest.approx(0.5 + PLATING)
    # On B.Cu the pad is an island of its own: one equipotential, nothing to refine.
    assert layers["B.Cu"]["fed_pads"] == [] and layers["B.Cu"]["relief_mm"] == 0.0


@pytest.mark.parametrize("sides,growth,rel", [(48, 0.2, 8e-3), (96, 0.15, 3e-3)])
def test_spoke_fed_pad_matches_closed_form_with_refinement(tmp_path, sides, growth, rel):
    total, _ = expected_resistance()
    assert solve(tmp_path, 0.5, sides, growth)[0] == pytest.approx(total, rel=rel)


def test_refinement_is_what_resolves_the_spoke(tmp_path):
    total, _ = expected_resistance()
    plain, _, _, plain_mesh = solve(tmp_path, 0.5)
    fine, _, _, fine_mesh = solve(tmp_path, 0.5, 96, 0.15)
    # h = 0.5 mm puts one or two elements across the 0.5 mm spoke: about 3.6 % low.
    assert plain / total - 1 < -0.02
    assert abs(fine / total - 1) < 0.2 * abs(plain / total - 1)
    assert len(fine_mesh["F.Cu"][0]) > len(plain_mesh["F.Cu"][0])
    assert len(fine_mesh["B.Cu"][0]) == len(plain_mesh["B.Cu"][0])  # the island is left alone


def test_missing_thermal_gap_refines_the_pad_only(tmp_path):
    total, _ = expected_resistance()
    value, board, layers, _ = solve(tmp_path, 0.5, 96, 0.15, thermal_gap=None)
    assert board["relief"] == {"F.Cu": 0.0} and layers["F.Cu"]["relief_mm"] == 0.0
    # Still refined at the pad, so far better than the plain mesh, but the far end of the
    # spoke is coarser than with the gap known.
    assert value == pytest.approx(total, rel=1.5e-2)


def test_pad_refinement_that_cannot_refine_is_rejected(tmp_path):
    path = tmp_path / "board.kicad_pcb"
    path.write_text(board_text(SPOKE_LENGTH))
    board = exp08.read_net(path, "PWR")
    layers = exp08.build_layers(board, PLATING, rim_segments=16)
    with pytest.raises(ValueError, match="pad J9.1"):  # 2 pi 0.525 / 16 = 0.206 mm >= h
        exp08.mesh_layer("F.Cu", layers["F.Cu"], board["pads"], tmp_path, 0.2, "gmsh", growth=0.2)

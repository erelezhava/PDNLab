"""End-to-end check of the net prototype (board file -> polygons -> Gmsh -> FEM) on a strip.

Needs a gmsh executable (skipped without one, like the other Gmsh tests). shapely is a
project dependency, so a missing shapely is an error, not a skip.
"""

import importlib.util
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
THICKNESS_MM = 0.035

# A 22 x 10 mm plane on F.Cu with two full-width 1 mm end pads (equipotential), plus one
# unconnected pad. Between the pads' inner edges the plane is 20 mm long and 10 mm wide.
BOARD = '''(kicad_pcb
 (layers (0 "F.Cu" signal) (2 "B.Cu" signal))
 (setup (stackup
  (layer "F.Cu" (type "copper") (thickness 0.035))
  (layer "dielectric 1" (type "core") (thickness 1.53))
  (layer "B.Cu" (type "copper") (thickness 0.035))))
 (footprint "a" (at 0 0) (property "Reference" "P1")
  (pad "1" smd rect (at 0.5 5) (size 1 10) (layers "F.Cu") (net "PWR") (pintype "passive")))
 (footprint "b" (at 0 0) (property "Reference" "P2")
  (pad "1" smd rect (at 21.5 5) (size 1 10) (layers "F.Cu") (net "PWR") (pintype "passive")))
 (footprint "c" (at 0 0) (property "Reference" "P3")
  (pad "1" smd rect (at 30 5) (size 1 2) (layers "F.Cu") (net "PWR") (pintype "passive")))
 (zone (net "PWR") (layer "F.Cu")
  (filled_polygon (layer "F.Cu") (pts (xy 0 0) (xy 22 0) (xy 22 10) (xy 0 10)))))'''


def solve(tmp_path, h):
    path = tmp_path / "strip.kicad_pcb"
    path.write_text(BOARD)
    board = exp08.read_net(path, "PWR")
    layers = exp08.build_layers(board, 0.025)
    meshes = {n: exp08.mesh_layer(n, layers[n], board["pads"], tmp_path, h, "gmsh")
              for n in board["copper"] if layers[n]["polys"]}
    model = exp08.assemble(board, layers, meshes, RHO, 0.025)
    return exp08.transfer_matrix(model, ["P2.1"], ["P1.1", "P3.1"])


# Pad outlines are forced into the mesh as nodes, not as conforming edges, so very coarse
# meshes tilt the effective electrode; the error shrinks quickly with the mesh size.
@pytest.mark.parametrize("h,rel", [(2.0, 2e-2), (0.5, 2e-3), (0.25, 1e-3)])
def test_strip_resistance_matches_closed_form(tmp_path, h, rel):
    loads, Z, unreachable, _ = solve(tmp_path, h)
    expected = RHO / (THICKNESS_MM * 1e-3) * (20 / 10)  # rho/t * L/W, ohms
    assert loads == ["P1.1"]
    assert Z[0, 0] == pytest.approx(expected, rel=rel)


def test_disconnected_pad_is_reported_unreachable_not_solved(tmp_path):
    _, _, unreachable, _ = solve(tmp_path, 1.0)
    assert unreachable == ["P3.1"]

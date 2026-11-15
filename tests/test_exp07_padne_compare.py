"""Checks of the padne comparison helpers on synthetic data. No padne, Gmsh or board needed."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "experiments" / "exp07_padne_compare.py"
spec = importlib.util.spec_from_file_location("exp07_padne_compare", SCRIPT)
cmp7 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cmp7)

PIECE = """<Piece NumberOfPoints="{n}" NumberOfCells="1">
<PointData Scalars="voltage"><DataArray type="Float64" format="ascii" Name="voltage">{v}</DataArray></PointData>
<Points><DataArray type="Float64" format="ascii" NumberOfComponents="3">{p}</DataArray></Points>
<Cells><DataArray type="Int32" format="ascii" Name="connectivity">0 1 2</DataArray></Cells>
</Piece>"""


def write_vtu(path, pieces):
    body = "".join(PIECE.format(n=len(v), v=" ".join(map(str, v)), p=" ".join(f"{x} {y} 0.0" for x, y in pts))
                   for pts, v in pieces)
    path.write_text(f'<?xml version="1.0"?><VTKFile type="UnstructuredGrid"><UnstructuredGrid>{body}'
                    "</UnstructuredGrid></VTKFile>", encoding="utf-8")


def test_reader_joins_pieces_and_restores_the_sign_of_y(tmp_path):
    # padne exports (x, -y, 0); board coordinates must come back with +y.
    write_vtu(tmp_path / "F.Cu.vtu", [([(1.0, -2.0), (3.0, -4.0), (5.0, -6.0)], [3.3, 3.2, 3.1]),
                                      ([(7.0, -8.0), (9.0, -10.0), (11.0, -12.0)], [0.0, 0.0, 0.0])])
    points, voltage = cmp7.read_padne_vtu(tmp_path / "F.Cu.vtu")
    assert points.tolist() == [[1, 2], [3, 4], [5, 6], [7, 8], [9, 10], [11, 12]]
    assert voltage.tolist() == [3.3, 3.2, 3.1, 0.0, 0.0, 0.0]


def test_reader_rejects_a_file_without_pieces(tmp_path):
    write_vtu(tmp_path / "empty.vtu", [])
    with pytest.raises(ValueError, match="no mesh pieces"):
        cmp7.read_padne_vtu(tmp_path / "empty.vtu")


def test_vertex_voltage_needs_a_vertex_at_the_pad_centre():
    points = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    voltage = np.array([3.3, 3.2, 3.1])
    assert cmp7.vertex_voltage(points, voltage, (1.0, 0.0)) == 3.2
    # A nearby vertex is not accepted: its voltage is not the pad-centre voltage.
    with pytest.raises(ValueError, match="no mesh vertex"):
        cmp7.vertex_voltage(points, voltage, (1.0, 0.01))


def test_ring_mean_uses_only_the_nodes_inside_the_ring():
    points = np.array([[0.1, 0.0], [0.2, 0.0], [0.0, 0.3], [0.5, 0.0]])
    values = np.array([10.0, 1.0, 3.0, 100.0])
    mean, count = cmp7.ring_mean(points, values, (0.0, 0.0), 0.175, 0.325)
    assert count == 2 and mean == pytest.approx(2.0)
    with pytest.raises(ValueError, match="no nodes"):
        cmp7.ring_mean(points, values, (0.0, 0.0), 0.6, 0.7)


def test_gate_is_50_uV_plus_5_percent_of_the_reference_drop():
    # Reference drop 2 mV: limit is 0.05 + 0.1 = 0.15 mV.
    assert cmp7.gate(2.0, 2.15 - 1e-9)
    assert cmp7.gate(2.0, 1.85 + 1e-9)
    assert not cmp7.gate(2.0, 2.15 + 1e-6)
    # Near zero only the absolute floor is left.
    assert cmp7.gate(0.0, 0.05 - 1e-9) and not cmp7.gate(0.0, 0.05 + 1e-6)


def test_feed_via_is_the_nearest_via_of_the_net():
    board = dict(pads=[dict(ref="J4", pin="6", centre=(10.0, 5.0))],
                 vias=[dict(c=(10.0, 6.4), drill=0.3), dict(c=(12.0, 5.0), drill=0.2)])
    assert cmp7.feed_vias(board, ["J4.6"])["J4.6"]["c"] == (10.0, 6.4)

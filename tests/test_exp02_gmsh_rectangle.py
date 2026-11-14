"""Exercise the real external mesher and independently check physical tags."""

import shutil

import meshio
import numpy as np
import pytest

from pdnlab_dc.fem import assemble_sheet, resistance_between
from pdnlab_dc.gmsh_mesh import read_sheet_mesh, run_gmsh, write_rectangle_geo


@pytest.mark.skipif(shutil.which("gmsh") is None, reason="external Gmsh not installed")
@pytest.mark.parametrize("length,width,size", [(0.05, 0.005, 0.005), (0.05, 0.005, 0.001), (0.1, 0.0002, 0.0002), (0.002, 0.08, 0.001)])
def test_gmsh_rectangle(tmp_path, length, width, size):
    geo, msh = tmp_path / "sheet.geo", tmp_path / "sheet.msh"
    write_rectangle_geo(geo, length, width, size)
    version = run_gmsh(geo, msh)
    assert version
    assert msh.read_text().splitlines()[1].startswith("4.1 0")
    mesh = read_sheet_mesh(msh)
    # Coordinates are used only as an independent test of tag extraction.
    left, right = mesh.boundaries["left"], mesh.boundaries["right"]
    np.testing.assert_array_equal(left, np.flatnonzero(np.isclose(mesh.nodes[:, 0], 0, atol=1e-12, rtol=0)))
    np.testing.assert_array_equal(right, np.flatnonzero(np.isclose(mesh.nodes[:, 0], length, atol=1e-12, rtol=0)))
    rho, thickness = 1.724e-8, 35e-6
    matrix = assemble_sheet(mesh.nodes, mesh.triangles, thickness / rho)
    resistance, v = resistance_between(matrix, left, right)
    assert resistance == pytest.approx(rho * length / (width * thickness), rel=1e-6)
    np.testing.assert_allclose(v, 1 - mesh.nodes[:, 0] / length, atol=1e-6, rtol=0)


def test_physical_names_not_entity_ids_and_unused_node_compaction(tmp_path):
    # A tiny MSH 2 fixture also tests ingestion without an installed executable.
    # Node 0 is unused. Physical IDs differ from geometric entity IDs.
    raw = meshio.Mesh(
        points=[[9, 9, 0], [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
        cells=[("line", [[4, 1], [2, 3]]), ("triangle", [[1, 2, 3], [1, 3, 4]])],
        cell_data={"gmsh:physical": [np.array([101, 202]), np.array([303, 303])],
                   "gmsh:geometrical": [np.array([4, 2]), np.array([1, 1])]},
        field_data={"left": np.array([101, 1]), "right": np.array([202, 1]), "copper": np.array([303, 2])},
    )
    path = tmp_path / "tags.msh"
    meshio.write(path, raw, file_format="gmsh22", binary=False)
    mesh = read_sheet_mesh(path)
    np.testing.assert_array_equal(mesh.boundaries["left"], [0, 3])
    np.testing.assert_array_equal(mesh.boundaries["right"], [1, 2])
    assert len(mesh.nodes) == 4
    assert mesh.triangles.max() == 3
    with pytest.raises(ValueError, match="missing physical group"):
        read_sheet_mesh(path, boundaries=("missing",))


def test_missing_executable(tmp_path):
    geo = tmp_path / "rectangle.geo"
    write_rectangle_geo(geo, 1, 1, 0.1)
    with pytest.raises(RuntimeError, match="not found"):
        run_gmsh(geo, tmp_path / "rectangle.msh", executable=str(tmp_path / "absent-gmsh"))

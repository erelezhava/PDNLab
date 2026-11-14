"""Small phase-0 Gmsh adapter. Gmsh is called only as an external program.

All geometry is in metres. This currently supports one planar, linear-triangle
sheet with named physical line groups; it is not a general board mesher.
"""

from dataclasses import dataclass
from pathlib import Path
import subprocess

import meshio
import numpy as np


@dataclass
class SheetMesh:
    nodes: np.ndarray
    triangles: np.ndarray
    boundaries: dict[str, np.ndarray]


def write_rectangle_geo(path, length, width, mesh_size):
    """Write a rectangle with physical groups copper, left and right."""
    values = np.asarray([length, width, mesh_size], dtype=float)
    if not np.isfinite(values).all() or np.any(values <= 0):
        raise ValueError("length, width and mesh_size must be finite and positive")
    Path(path).write_text(
        f"""// SI units: metres. Physical IDs intentionally differ from entity IDs.
Point(1) = {{0, 0, 0, {mesh_size:.17g}}};
Point(2) = {{{length:.17g}, 0, 0, {mesh_size:.17g}}};
Point(3) = {{{length:.17g}, {width:.17g}, 0, {mesh_size:.17g}}};
Point(4) = {{0, {width:.17g}, 0, {mesh_size:.17g}}};
Line(1) = {{1, 2}};
Line(2) = {{2, 3}};
Line(3) = {{3, 4}};
Line(4) = {{4, 1}};
Curve Loop(1) = {{1, 2, 3, 4}};
Plane Surface(1) = {{1}};
Physical Curve("left", 101) = {{4}};
Physical Curve("right", 202) = {{2}};
Physical Surface("copper", 303) = {{1}};
Mesh.ElementOrder = 1;
Mesh.MshFileVersion = 4.1;
Mesh.Binary = 0;
""", encoding="utf-8"
    )


def write_annulus_geo(path, inner_radius, outer_radius, mesh_size):
    """Write a circular annulus with physical groups copper, inner and outer.

    Each circle has four exact quarter-circle CAD arcs. First-order mesh edges
    approximate those arcs by chords; refinement reduces that geometry error
    along with the finite-element error.
    """
    values = np.asarray([inner_radius, outer_radius, mesh_size], dtype=float)
    if not np.isfinite(values).all() or np.any(values <= 0) or inner_radius >= outer_radius:
        raise ValueError("require finite 0 < inner_radius < outer_radius and mesh_size > 0")
    lines = ["// SI units: metres.", f"Point(1) = {{0, 0, 0, {mesh_size:.17g}}};"]
    for start, radius in [(2, outer_radius), (6, inner_radius)]:
        for offset, (x, y) in enumerate([(radius, 0), (0, radius), (-radius, 0), (0, -radius)]):
            lines.append(f"Point({start + offset}) = {{{x:.17g}, {y:.17g}, 0, {mesh_size:.17g}}};")
    for arc_start, point_start in [(1, 2), (11, 6)]:
        for i in range(4):
            lines.append(f"Circle({arc_start + i}) = {{{point_start + i}, 1, {point_start + (i + 1) % 4}}};")
    lines.extend([
        "Curve Loop(1) = {1, 2, 3, 4};",
        "Curve Loop(2) = {11, 12, 13, 14};",
        "Plane Surface(1) = {1, 2};",
        'Physical Curve("inner", 101) = {11, 12, 13, 14};',
        'Physical Curve("outer", 202) = {1, 2, 3, 4};',
        'Physical Surface("copper", 303) = {1};',
        "Mesh.ElementOrder = 1;",
        "Mesh.MshFileVersion = 4.1;",
        "Mesh.Binary = 0;",
    ])
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_gmsh(args, executable, timeout):
    try:
        result = subprocess.run(
            [str(executable), *args], capture_output=True, text=True,
            check=True, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Gmsh executable not found; install Gmsh and add it to PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Gmsh failed:\n{exc.stdout}\n{exc.stderr}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Gmsh exceeded {timeout} seconds") from exc
    return result


def run_gmsh(geo_path, msh_path, executable="gmsh", timeout=60):
    """Mesh a .geo file to ASCII MSH 4.1; return the executable's version."""
    geo_path, msh_path = Path(geo_path).resolve(), Path(msh_path).resolve()
    if not geo_path.is_file():
        raise FileNotFoundError(geo_path)
    # Never mistake a previous output for a successful new mesh.
    msh_path.unlink(missing_ok=True)
    result = _run_gmsh(["--version"], executable, timeout)
    version = (result.stdout.strip() or result.stderr.strip())
    _run_gmsh(
        [str(geo_path), "-2", "-format", "msh4", "-o", str(msh_path), "-v", "2"],
        executable, timeout,
    )
    if not msh_path.is_file() or msh_path.stat().st_size == 0:
        raise RuntimeError("Gmsh did not produce a mesh")
    return version


def read_sheet_mesh(path, surface="copper", boundaries=("left", "right")):
    """Read tagged surface triangles and line groups, compacting node indices.

    Terminals come from physical tags, never from coordinate comparisons.
    Multiple cell blocks with the same type/tag are combined by meshio.
    """
    mesh = meshio.read(path)
    cells = mesh.cells_dict
    tags = mesh.cell_data_dict.get("gmsh:physical", {})

    def group(name, dimension, cell_type):
        if name not in mesh.field_data:
            raise ValueError(f"missing physical group {name!r}")
        tag, dim = mesh.field_data[name]
        if dim != dimension:
            raise ValueError(f"physical group {name!r} must have dimension {dimension}")
        if cell_type not in cells or cell_type not in tags:
            raise ValueError(f"missing tagged {cell_type} elements for {name!r}")
        selected = cells[cell_type][tags[cell_type] == tag]
        if selected.size == 0:
            raise ValueError(f"physical group {name!r} contains no {cell_type} elements")
        return selected

    triangles = group(surface, 2, "triangle")
    used = np.unique(triangles)
    points = mesh.points[used]
    if not np.isfinite(points).all() or not np.allclose(points[:, 2], 0, atol=1e-12, rtol=0):
        raise ValueError("sheet must be finite and lie in the z=0 plane")
    mapping = np.full(len(mesh.points), -1, dtype=np.int64)
    mapping[used] = np.arange(len(used))
    boundary_nodes = {}
    for name in boundaries:
        indices = np.unique(group(name, 1, "line"))
        if np.any(mapping[indices] < 0):
            raise ValueError(f"boundary {name!r} has nodes outside the selected surface")
        boundary_nodes[name] = mapping[indices]
    return SheetMesh(points[:, :2], mapping[triangles], boundary_nodes)

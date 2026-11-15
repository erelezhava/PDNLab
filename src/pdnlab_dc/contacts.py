"""Finite-area contacts on an existing linear-triangle mesh, in SI units.

Uniform-current patches integrate each shape function over the actual region;
they do not select nearest vertices or force the copper equipotential.
Equipotential electrodes require a mesh conforming to the region boundary.
No remeshing, contact resistivity or 3D interface physics is inferred here.
"""

from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import Polygon

from .fem import _indices


@dataclass(frozen=True)
class AreaContact:
    """Selection in original mesh indices; weights=None means equipotential.

    Uniform-current weights also evaluate the area-average FEM voltage.
    Support vertices may lie outside the patch: weights are integrals of
    linear basis functions, not a geometrical enlargement of the contact.
    """

    model: str
    nodes: np.ndarray
    weights: np.ndarray | None
    requested_area_m2: float
    covered_area_m2: float
    bounds_m: tuple[float, float, float, float]


def select_area_contact(nodes, triangles, region, *, model="equipotential"):
    """Return a finite-area electrode or uniform-current nodal selection.

    region is an explicit valid Shapely Polygon in mesh coordinates (metres),
    optionally with holes. Its entire area must lie on nonoverlapping copper
    triangles. Coverage checks allow relative geometric roundoff of 1e-10.
    An equipotential region must be a union of complete mesh triangles;
    materially cut triangles raise, requesting a conforming mesh.
    """
    if model not in ("equipotential", "uniform_current"):
        raise ValueError("contact model must be equipotential or uniform_current")
    if (not isinstance(region, Polygon) or region.is_empty or not region.is_valid
            or not np.isfinite(shapely.get_coordinates(region)).all()
            or region.has_z or not np.isfinite(region.area) or region.area <= 0):
        raise ValueError("contact region must be a valid finite positive-area 2D Polygon")
    nodes = np.asarray(nodes, dtype=float)
    if nodes.ndim != 2 or nodes.shape[1] != 2 or not np.isfinite(nodes).all():
        raise ValueError("nodes must be finite with shape (N, 2)")
    triangles = _indices(triangles, len(nodes), "triangles", 2)
    if triangles.shape[1] != 3:
        raise ValueError("triangles must have shape (M, 3)")
    points = nodes[triangles]
    edges = points[:, 1:]-points[:, :1]
    areas = .5*np.abs(edges[:, 0, 0]*edges[:, 1, 1]-edges[:, 0, 1]*edges[:, 1, 0])
    if not np.isfinite(areas).all() or np.any(areas <= 0):
        raise ValueError("triangle areas must be finite and positive")
    lo, hi = points.min(axis=1), points.max(axis=1)
    xmin, ymin, xmax, ymax = region.bounds
    candidates = np.flatnonzero((hi[:, 0] >= xmin) & (lo[:, 0] <= xmax)
                                & (hi[:, 1] >= ymin) & (lo[:, 1] <= ymax))
    pieces = shapely.intersection(shapely.polygons(points[candidates]), region)
    piece_areas = shapely.area(pieces)
    positive = piece_areas > 0
    candidates, pieces, piece_areas = candidates[positive], pieces[positive], piece_areas[positive]
    requested = float(region.area)
    geometric_rtol = 1e-10
    tolerance = geometric_rtol*requested
    covered = shapely.union_all(pieces)
    covered_area = float(covered.area)
    if region.difference(covered).area > tolerance:
        raise ValueError("contact region is not fully covered by sheet copper")
    if float(piece_areas.sum())-covered_area > tolerance:
        raise ValueError("overlapping copper triangles within contact region")
    if model == "equipotential":
        fraction = piece_areas/areas[candidates]
        cut = (fraction > geometric_rtol) & (fraction < 1-geometric_rtol)
        if np.any(cut):
            raise ValueError("equipotential contact requires a conforming mesh; region cuts triangles")
        complete = candidates[fraction >= 1-geometric_rtol]
        # Prevent a small patch from being represented by zero complete elements.
        if len(complete) == 0 or abs(float(areas[complete].sum())-requested) > tolerance:
            raise ValueError("equipotential contact requires a conforming mesh")
        selected = np.unique(triangles[complete])
        weights = None
    else:
        # For affine N_i, integral_A N_i = area(A)*N_i(centroid(A)), even if
        # the clipped piece has holes or multiple disconnected parts.
        centres = shapely.centroid(pieces)
        xy = np.column_stack([shapely.get_x(centres), shapely.get_y(centres)])
        local = np.linalg.solve(edges[candidates].transpose(0, 2, 1),
                                (xy-points[candidates, 0])[..., None])[..., 0]
        barycentric = np.column_stack([1-local.sum(axis=1), local])
        if (not np.isfinite(barycentric).all() or np.any(barycentric < -geometric_rtol)
                or np.any(barycentric > 1+geometric_rtol)):
            raise ValueError("contact integration is ill-conditioned; refine or repair mesh")
        integrated = np.zeros(len(nodes))
        np.add.at(integrated, triangles[candidates].ravel(),
                  (piece_areas[:, None]*np.clip(barycentric, 0., 1.)).ravel())
        selected = np.flatnonzero(integrated > 0)
        if not len(selected):
            raise ValueError("contact has no resolvable integration support")
        weights = integrated[selected]/integrated[selected].sum()
        weights.flags.writeable = False
    selected.flags.writeable = False
    return AreaContact(model, selected, weights, requested, covered_area, tuple(region.bounds))

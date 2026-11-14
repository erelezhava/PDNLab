"""Linear-triangle finite elements for one thin conducting sheet.

Units are SI throughout: metres, volts, amperes, siemens.

The sheet obeys div(g grad v) = 0, where g = sigma * t is the sheet
conductance in siemens (the inverse of sheet resistance in ohms per square).
With one voltage per mesh node and linear interpolation inside each triangle,
the result is an ordinary nodal conductance matrix: every triangle edge acts
as a resistor of conductance g * cot(opposite angle) / 2.
"""

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import spsolve


def assemble_sheet(nodes, triangles, sheet_conductance):
    """Build the nodal conductance matrix of a triangulated sheet.

    nodes: (N, 2) node coordinates in metres.
    triangles: (M, 3) node indices of each triangle, any orientation.
    sheet_conductance: sigma * t in siemens, one value or one per triangle.

    Returns an (N, N) sparse matrix G in siemens, so that G @ v is the
    current each node receives from outside the mesh.
    """
    nodes = np.asarray(nodes, dtype=float)
    triangles = np.asarray(triangles, dtype=np.int64)
    n_nodes = nodes.shape[0]
    n_tri = triangles.shape[0]
    g = np.broadcast_to(np.asarray(sheet_conductance, dtype=float), (n_tri,))

    p0 = nodes[triangles[:, 0]]
    p1 = nodes[triangles[:, 1]]
    p2 = nodes[triangles[:, 2]]

    # Edge opposite each corner. The gradient of corner i's shape function is
    # that edge turned by 90 degrees and divided by twice the area, so
    # grad N_i . grad N_j = (e_i . e_j) / (4 A^2).
    edges = np.stack([p2 - p1, p0 - p2, p1 - p0], axis=1)  # (M, 3, 2)
    d1 = p1 - p0
    d2 = p2 - p0
    area = 0.5 * np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])
    if np.any(area <= 0.0):
        bad = np.flatnonzero(area <= 0.0)
        raise ValueError(f"zero-area triangles at indices {bad[:10].tolist()}")

    # Element matrix: g * A * grad N_i . grad N_j = g * (e_i . e_j) / (4 A)
    dots = np.einsum("mik,mjk->mij", edges, edges)
    element = (g / (4.0 * area))[:, None, None] * dots  # (M, 3, 3)

    rows = np.repeat(triangles, 3, axis=1).ravel()
    cols = np.tile(triangles, (1, 3)).ravel()
    matrix = sp.coo_matrix((element.ravel(), (rows, cols)), shape=(n_nodes, n_nodes))
    return matrix.tocsr()


def solve_fixed_voltages(matrix, fixed_nodes, fixed_voltages, injected_current=None):
    """Solve for all node voltages with some nodes held at given voltages.

    matrix: nodal conductance matrix from assemble_sheet.
    fixed_nodes: indices of nodes with a prescribed voltage.
    fixed_voltages: their voltages in volts, one value or one per node.
    injected_current: optional (N,) current fed into each node in amperes.
        Values at fixed nodes are ignored.

    Every connected piece of the mesh must contain at least one fixed node,
    otherwise the system is singular.
    """
    n_nodes = matrix.shape[0]
    fixed_nodes = np.asarray(fixed_nodes, dtype=np.int64)
    if np.unique(fixed_nodes).size != fixed_nodes.size:
        raise ValueError("fixed_nodes contains duplicates")

    v = np.zeros(n_nodes)
    v[fixed_nodes] = fixed_voltages

    is_free = np.ones(n_nodes, dtype=bool)
    is_free[fixed_nodes] = False
    free_nodes = np.flatnonzero(is_free)
    if free_nodes.size == 0:
        return v

    rhs = -(matrix[free_nodes][:, fixed_nodes] @ v[fixed_nodes])
    if injected_current is not None:
        rhs = rhs + np.asarray(injected_current, dtype=float)[free_nodes]

    free_matrix = matrix[free_nodes][:, free_nodes].tocsc()
    v[free_nodes] = spsolve(free_matrix, rhs)
    return v


def node_currents(matrix, v):
    """Current each node receives from outside the mesh, in amperes.

    At a free node this equals the injected current (zero if none). At a
    fixed node it is the reaction: the current the voltage source feeds in
    (positive) or takes out (negative).
    """
    return matrix @ v


def resistance_between(matrix, high_nodes, low_nodes):
    """Resistance in ohms between two groups of nodes.

    Holds high_nodes at 1 V and low_nodes at 0 V and takes the current from
    the reaction at high_nodes. Returns (resistance, node voltages).
    """
    high_nodes = np.asarray(high_nodes, dtype=np.int64)
    low_nodes = np.asarray(low_nodes, dtype=np.int64)
    fixed_nodes = np.concatenate([high_nodes, low_nodes])
    fixed_voltages = np.concatenate([np.ones(high_nodes.size), np.zeros(low_nodes.size)])
    v = solve_fixed_voltages(matrix, fixed_nodes, fixed_voltages)
    current = node_currents(matrix, v)[high_nodes].sum()
    return 1.0 / current, v

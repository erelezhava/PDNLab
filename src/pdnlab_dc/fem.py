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
from scipy.sparse.csgraph import connected_components


def _indices(values, size, name, ndim):
    """Reject malformed indices before integer conversion can hide mistakes."""
    raw = np.asarray(values)
    if raw.ndim != ndim or raw.size == 0:
        raise ValueError(f"{name} must be a nonempty {ndim}D array")
    if raw.dtype.kind not in "iu" or np.any(raw < 0) or np.any(raw >= size):
        raise ValueError(f"{name} must contain integer indices in [0, {size})")
    return raw.astype(np.int64)


def _network_references(matrix, fixed_nodes):
    """Check a conservative nodal network and choose one origin per component.

    Ground is an explicit node, not an implicit shunt. Only roundoff-sized
    row sums are accepted before using the constant-voltage nullspace. The
    64-epsilon bound relative to each row's absolute sum allows accumulation
    in FEM assembly/ties; it is not a current/power acceptance tolerance.
    """
    row_sum = np.asarray(matrix.sum(axis=1)).ravel()
    row_scale = np.asarray(abs(matrix).sum(axis=1)).ravel()
    if (not np.isfinite(row_sum).all() or not np.isfinite(row_scale).all()
            or np.any(np.abs(row_sum) > 64*np.finfo(float).eps*row_scale)):
        raise ValueError("conductance matrix must have zero row sums within roundoff; "
                         "use an explicit fixed node for ground")
    graph = matrix
    if np.any(matrix.data == 0):
        graph = matrix.copy()
        graph.eliminate_zeros()
    count, labels = connected_components(graph, directed=False)
    anchors = np.full(count, matrix.shape[0], dtype=int)
    np.minimum.at(anchors, labels[fixed_nodes], fixed_nodes)
    missing = anchors == matrix.shape[0]
    if np.any(missing):
        floating = np.flatnonzero(missing[labels])
        raise ValueError(f"unanchored component: nodes {floating[:10].tolist()}")
    return anchors[labels], labels


def assemble_sheet(nodes, triangles, sheet_conductance):
    """Build the nodal conductance matrix of a triangulated sheet.

    nodes: (N, 2) node coordinates in metres.
    triangles: (M, 3) node indices of each triangle, any orientation.
    sheet_conductance: sigma * t in siemens, one value or one per triangle.

    Returns an (N, N) sparse matrix G in siemens, so that G @ v is the
    current each node receives from outside the mesh.
    """
    nodes = np.asarray(nodes, dtype=float)
    if nodes.ndim != 2 or nodes.shape[1] != 2 or len(nodes) < 3:
        raise ValueError("nodes must have shape (N, 2), with N >= 3")
    if not np.isfinite(nodes).all():
        raise ValueError("nodes must be finite")
    triangles = _indices(triangles, len(nodes), "triangles", 2)
    if triangles.shape[1] != 3:
        raise ValueError("triangles must have shape (M, 3)")
    n_nodes = nodes.shape[0]
    n_tri = triangles.shape[0]
    g = np.broadcast_to(np.asarray(sheet_conductance, dtype=float), (n_tri,))
    if not np.isfinite(g).all() or np.any(g <= 0):
        raise ValueError("sheet_conductance must be finite and positive")

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
    if not np.isfinite(area).all():
        raise ValueError("triangle areas must be finite")
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

    Every connected piece must contain at least one fixed node; otherwise
    ValueError is raised. Floating copper removal belongs to the future board
    preprocessing stage, not this low-level solver.
    """
    # Compatibility entry point: callers with repeated loads should prepare once.
    from .solver import prepare_fixed_voltages

    return prepare_fixed_voltages(matrix, fixed_nodes).solve(
        fixed_voltages, injected_current)


def node_currents(matrix, v):
    """Current each node receives from outside the mesh, in amperes.

    At a free node this equals the injected current (zero if none). At a
    fixed node it is the reaction: the current the voltage source feeds in
    (positive) or takes out (negative).
    """
    return matrix @ v


def tie_nodes(matrix, groups):
    """Merge each disjoint group into one equipotential electrical node.

    Returns (reduced_matrix, node_map). Original voltages are recovered with
    reduced_voltages[node_map]. A current vector is reduced by summing its
    entries with the same map index, e.g. np.bincount(node_map, weights=current).

    This stamps G_reduced = P.T @ G @ P, where P maps reduced voltages to the
    original nodes. Geometry is unchanged: only electrical unknowns are merged.
    Separate via rims on different layers must remain separate groups.
    """
    matrix = sp.csr_matrix(matrix, dtype=float)
    n = matrix.shape[0]
    if n == 0 or matrix.shape != (n, n) or not np.isfinite(matrix.data).all():
        raise ValueError("matrix must be nonempty, square and finite")
    representative = np.arange(n)
    used = np.zeros(n, dtype=bool)
    for group in groups:
        indices = _indices(group, n, "tie group", 1)
        if np.unique(indices).size != indices.size or np.any(used[indices]):
            raise ValueError("tie groups must be disjoint and contain no duplicates")
        used[indices] = True
        representative[indices] = indices.min()
    _, node_map = np.unique(representative, return_inverse=True)
    projection = sp.csr_matrix(
        (np.ones(n), (np.arange(n), node_map)), shape=(n, node_map.max() + 1)
    )
    reduced = (projection.T @ matrix @ projection).tocsr()
    reduced.eliminate_zeros()
    return reduced, node_map


def add_resistors(matrix, node_pairs, resistances):
    """Return a new conductance matrix with lumped two-terminal resistors.

    node_pairs has shape (M, 2); resistances is a positive scalar or (M,) in
    ohms. Parallel branches are allowed. For a via, stamp between the two
    *distinct* layer rim nodes after applying equipotential ties.
    """
    matrix = sp.csr_matrix(matrix, dtype=float)
    n = matrix.shape[0]
    if n == 0 or matrix.shape != (n, n) or not np.isfinite(matrix.data).all():
        raise ValueError("matrix must be nonempty, square and finite")
    pairs = _indices(node_pairs, n, "node_pairs", 2)
    if pairs.shape[1] != 2:
        raise ValueError("node_pairs must have shape (M, 2)")
    if np.any(pairs[:, 0] == pairs[:, 1]):
        raise ValueError("resistor terminals must be distinct")
    r = np.broadcast_to(np.asarray(resistances, dtype=float), (len(pairs),))
    if not np.isfinite(r).all() or np.any(r <= 0):
        raise ValueError("resistances must be finite and positive")
    with np.errstate(over="ignore"):
        g = 1.0 / r
    if not np.isfinite(g).all():
        raise ValueError("resistor conductance is not representable")
    a, b = pairs.T
    rows = np.column_stack([a, a, b, b]).ravel()
    cols = np.column_stack([a, b, a, b]).ravel()
    values = np.column_stack([g, -g, -g, g]).ravel()
    stamp = sp.coo_matrix((values, (rows, cols)), shape=matrix.shape).tocsr()
    return (matrix + stamp).tocsr()


def resistance_between(matrix, high_nodes, low_nodes):
    """Resistance in ohms between two groups of nodes.

    Holds high_nodes at 1 V and low_nodes at 0 V and takes the current from
    the reaction at high_nodes. Returns (resistance, node voltages).
    """
    high_nodes = _indices(high_nodes, matrix.shape[0], "high_nodes", 1)
    low_nodes = _indices(low_nodes, matrix.shape[0], "low_nodes", 1)
    fixed_nodes = np.concatenate([high_nodes, low_nodes])
    fixed_voltages = np.concatenate([np.ones(high_nodes.size), np.zeros(low_nodes.size)])
    v = solve_fixed_voltages(matrix, fixed_nodes, fixed_voltages)
    current = node_currents(matrix, v)[high_nodes].sum()
    if not np.isfinite(current) or current <= 0:
        raise ValueError("terminals have no positive finite conductance")
    return 1.0 / current, v

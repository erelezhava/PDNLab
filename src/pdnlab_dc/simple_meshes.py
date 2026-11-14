"""Hand-made meshes for analytic tests. No mesher involved."""

import numpy as np


def rectangle_mesh(length, width, nx, ny, diagonals="alternate", jitter=0.0, seed=0):
    """Triangulate a rectangle 0..length by 0..width.

    nx, ny: number of cells along the length and the width. Each cell is cut
        into two triangles.
    diagonals: "same" cuts every cell the same way, "alternate" flips the cut
        in a checkerboard pattern.
    jitter: moves interior nodes randomly by up to this fraction of a cell, to
        get irregular triangles. Keep it at or below 0.2.

    Returns (nodes, triangles, left_nodes, right_nodes). The left end is the
    edge x = 0, the right end is x = length.
    """
    if diagonals not in ("same", "alternate"):
        raise ValueError("diagonals must be 'same' or 'alternate'")

    xs = np.linspace(0.0, length, nx + 1)
    ys = np.linspace(0.0, width, ny + 1)
    grid_x, grid_y = np.meshgrid(xs, ys, indexing="ij")  # (nx+1, ny+1)
    nodes = np.column_stack([grid_x.ravel(), grid_y.ravel()])

    def index(i, j):
        return i * (ny + 1) + j

    if jitter > 0.0:
        rng = np.random.default_rng(seed)
        cell = np.array([length / nx, width / ny])
        for i in range(1, nx):
            for j in range(1, ny):
                nodes[index(i, j)] += jitter * cell * rng.uniform(-1.0, 1.0, size=2)

    triangles = []
    for i in range(nx):
        for j in range(ny):
            a = index(i, j)
            b = index(i + 1, j)
            c = index(i + 1, j + 1)
            d = index(i, j + 1)
            if diagonals == "alternate" and (i + j) % 2 == 1:
                triangles += [(a, b, d), (b, c, d)]
            else:
                triangles += [(a, b, c), (a, c, d)]

    left_nodes = np.array([index(0, j) for j in range(ny + 1)])
    right_nodes = np.array([index(nx, j) for j in range(ny + 1)])
    return nodes, np.array(triangles), left_nodes, right_nodes

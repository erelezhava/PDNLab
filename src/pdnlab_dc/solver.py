"""Prepared DC solves, independent of geometry/importers. All units are SI.

Positive prescribed current enters a node; a current sink is negative.
Preparation snapshots conductances and fixed-node membership. Changing either
requires a new instance; voltage values and currents may vary between solves.
Only sparse LU is implemented. Backend details stay inside this module.
"""

from dataclasses import dataclass
from time import perf_counter

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import splu

from .fem import _indices, _network_references


def _sparse_bytes(matrix):
    return matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes


@dataclass(frozen=True)
class PreparationStats:
    """factor_nnz is the entries SuperLU itself stores (its supernodal L and U), read
    without copying the factor. sparse_storage_bytes is the matrix and coupling as
    stored, plus the factor estimated at 12 bytes per entry (8 for the value, 4 for its
    row) and one 4-byte pointer per column for each of L and U."""
    preparation_s: float
    factorization_s: float
    nodes: int
    free_nodes: int
    matrix_nnz: int
    factor_nnz: int
    sparse_storage_bytes: int


@dataclass(frozen=True)
class SolveStats:
    elapsed_s: float
    rhs_columns: int
    max_batch_columns: int
    max_rhs_bytes: int


class PreparedSystem:
    """Reusable fixed-voltage system; construct via prepare_fixed_voltages.

    stats counts retained sparse array storage (G, coupling, L and U), not
    peak memory/RSS, SuperLU workspace or its opaque native allocations.
    last_solve describes the last successful solve/transfer request; instances
    are intended for sequential use, not concurrent calls.
    """

    def __init__(self, matrix, fixed_nodes):
        started = perf_counter()
        self._matrix = sp.csr_matrix(matrix, dtype=float, copy=True)
        n = self._matrix.shape[0]
        if n == 0 or self._matrix.shape != (n, n):
            raise ValueError("matrix must be nonempty and square")
        self._matrix.sum_duplicates()
        self._matrix.eliminate_zeros()
        if not np.isfinite(self._matrix.data).all():
            raise ValueError("matrix must be finite")
        self._fixed = _indices(fixed_nodes, n, "fixed_nodes", 1).copy()
        if len(np.unique(self._fixed)) != len(self._fixed):
            raise ValueError("fixed_nodes contains duplicates")
        self._references, _ = _network_references(self._matrix, self._fixed)
        mask = np.ones(n, dtype=bool)
        mask[self._fixed] = False
        self._free = np.flatnonzero(mask)
        self._coupling = self._matrix[self._free][:, self._fixed].tocsr()
        free_matrix = self._matrix[self._free][:, self._free].tocsc()
        factor_started = perf_counter()
        try:
            self._lu = splu(free_matrix) if len(self._free) else None
        except RuntimeError as exc:
            raise ValueError("conductance matrix is singular") from exc
        factorization_s = perf_counter() - factor_started
        # Not lu.L and lu.U: SciPy builds each as a new copy of the factor, which doubled
        # the factor's memory just to count it (measured 2026-11-25).
        factor_nnz = self._lu.nnz if self._lu is not None else 0
        factor_bytes = 12 * factor_nnz + 8 * (len(self._free) + 1) if factor_nnz else 0
        self.stats = PreparationStats(
            preparation_s=perf_counter() - started,
            factorization_s=factorization_s, nodes=n, free_nodes=len(self._free),
            matrix_nnz=self._matrix.nnz,
            factor_nnz=factor_nnz,
            sparse_storage_bytes=sum(_sparse_bytes(x) for x in (self._matrix, self._coupling))
            + factor_bytes,
        )
        self.last_solve = None

    @property
    def fixed_nodes(self):
        """Copy of the fixed indices, in the voltage-value input order."""
        return self._fixed.copy()

    def _solve_rhs(self, rhs):
        result = self._lu.solve(rhs)
        if not np.isfinite(result).all():
            raise ValueError("solve produced nonfinite voltages")
        return result

    def solve(self, fixed_voltages, injected_current=None):
        """Return (N,) voltages, reusing LU. No transfer matrix is built.

        fixed_voltages is scalar or (number of fixed nodes,). Current is (N,)
        and is ignored at fixed nodes, whose voltage sources supply reactions.
        Each connected network is solved relative to one fixed voltage, then
        shifted back. This avoids multiplying FEM row-sum roundoff by the rail
        voltage. The matrix must conserve current; ground is an explicit node.
        """
        started = perf_counter()
        n = self.stats.nodes
        values = np.asarray(fixed_voltages, dtype=float)
        try:
            values = np.broadcast_to(values, (len(self._fixed),))
        except ValueError as exc:
            raise ValueError("fixed_voltages must be scalar or one per fixed node") from exc
        if not np.isfinite(values).all():
            raise ValueError("fixed_voltages must be finite")
        current = np.zeros(n) if injected_current is None else np.asarray(injected_current, dtype=float)
        if current.shape != (n,) or not np.isfinite(current).all():
            raise ValueError("injected_current must be finite with shape (N,)")
        v = np.empty(n)
        v[self._fixed] = values
        reference = v[self._references]
        rhs = current[self._free] - self._coupling @ (values-reference[self._fixed])
        if self._lu is not None:
            v[self._free] = self._solve_rhs(rhs) + reference[self._free]
        if not np.isfinite(v).all():
            raise ValueError("solve produced nonfinite voltages")
        self.last_solve = SolveStats(perf_counter() - started, 1, 1, rhs.nbytes)
        return v

    def transfer_resistance(self, injection_nodes, observation_nodes=None, *, batch_size=32):
        """Return Z[observation, injection] in ohms with fixed voltages at zero.

        Columns are responses to +1 A at each selected electrical node. For
        sinks, positive voltage *drop* uses the same Z. Inputs at fixed nodes
        are ignored, giving zero columns. A tied pad/rim is one electrical
        node. A single untied sheet vertex is a mathematical point contact;
        its local voltage is mesh-dependent, not a physical contact model.

        RHS and full-field solutions are bounded by batch_size. The returned
        dense observation-by-injection matrix itself is not memory bounded.
        """
        started = perf_counter()
        n = self.stats.nodes
        inject = _indices(injection_nodes, n, "injection_nodes", 1)
        observe = inject if observation_nodes is None else _indices(
            observation_nodes, n, "observation_nodes", 1)
        if isinstance(batch_size, (bool, np.bool_)) or not isinstance(batch_size, (int, np.integer)) or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        positions = np.full(n, -1, dtype=int)
        positions[self._free] = np.arange(len(self._free))
        rows = positions[observe]
        active = rows >= 0
        output = np.zeros((len(observe), len(inject)))
        max_columns = min(batch_size, len(inject))
        for start in range(0, len(inject), batch_size):
            stop = min(start + batch_size, len(inject))
            if self._lu is None:
                continue
            rhs = np.zeros((len(self._free), stop - start), order="F")
            cols = positions[inject[start:stop]]
            valid = cols >= 0
            rhs[cols[valid], np.flatnonzero(valid)] = 1.0
            response = self._solve_rhs(rhs)
            output[active, start:stop] = response[rows[active]]
            del rhs, response
        self.last_solve = SolveStats(perf_counter() - started, len(inject),
                                    max_columns, len(self._free) * max_columns * 8)
        return output


def prepare_fixed_voltages(matrix, fixed_nodes):
    """Validate, snapshot and factor a network once for repeated load cases."""
    return PreparedSystem(matrix, fixed_nodes)

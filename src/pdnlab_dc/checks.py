"""Independent loss integration and phase-0 conservation gates (SI units).

Sheet loss is integrated from voltage gradients, not computed as v.T @ G @ v.
Lumped losses come from branch voltage differences. This lets the power check
catch errors in assembly, node mapping or loss bookkeeping.
"""

import numpy as np
import scipy.sparse as sp

from .fem import _indices, _network_references


def sheet_losses(nodes, triangles, sheet_conductance, voltages):
    """Return Joule loss per linear triangle: g * area * |grad(v)|², watts."""
    nodes = np.asarray(nodes, dtype=float)
    if nodes.ndim != 2 or nodes.shape[1] != 2 or not np.isfinite(nodes).all():
        raise ValueError("nodes must be finite with shape (N, 2)")
    triangles = _indices(triangles, len(nodes), "triangles", 2)
    if triangles.shape[1] != 3:
        raise ValueError("triangles must have shape (M, 3)")
    v = np.asarray(voltages, dtype=float)
    if v.shape != (len(nodes),) or not np.isfinite(v).all():
        raise ValueError("voltages must be finite with shape (N,)")
    g = np.broadcast_to(np.asarray(sheet_conductance, dtype=float), (len(triangles),))
    if not np.isfinite(g).all() or np.any(g <= 0):
        raise ValueError("sheet_conductance must be finite and positive")
    p = nodes[triangles]
    edges = p[:, 1:] - p[:, :1]
    area = np.abs(np.linalg.det(edges)) / 2
    if not np.isfinite(area).all() or np.any(area <= 0):
        raise ValueError("triangle areas must be finite and positive")
    dv = v[triangles[:, 1:]] - v[triangles[:, :1]]
    gradients = np.linalg.solve(edges, dv[..., None])[..., 0]
    losses = g * area * np.sum(gradients**2, axis=1)
    if not np.isfinite(losses).all():
        raise ValueError("sheet losses must be finite")
    return losses


def resistor_losses(voltages, node_pairs, resistances):
    """Return each lumped branch loss (V_a - V_b)²/R, watts."""
    v = np.asarray(voltages, dtype=float)
    if v.ndim != 1 or not np.isfinite(v).all():
        raise ValueError("voltages must be a finite vector")
    pairs = _indices(node_pairs, len(v), "node_pairs", 2)
    if pairs.shape[1] != 2 or np.any(pairs[:, 0] == pairs[:, 1]):
        raise ValueError("node_pairs must have shape (M, 2) with distinct terminals")
    r = np.broadcast_to(np.asarray(resistances, dtype=float), (len(pairs),))
    if not np.isfinite(r).all() or np.any(r <= 0):
        raise ValueError("resistances must be finite and positive")
    loss = (v[pairs[:, 0]] - v[pairs[:, 1]])**2 / r
    if not np.isfinite(loss).all():
        raise ValueError("resistor losses must be finite")
    return loss


def balance_checks(matrix, voltages, fixed_nodes, sheet_loss_w, lumped_loss_w=0.0,
                   injected_current=None, rim_currents=None, solver_rtol=0.0):
    """Check global current, power, and each supplied via rim separately.

    Reactions at fixed nodes plus prescribed currents at free nodes define the
    external terminals. Positive current enters the network. Power delivered
    is their signed sum (v-v_ref)*i, including current sources and absorbing
    terminals. Each connected network uses one fixed node as its origin and
    must independently conserve current. Losses are integrated independently
    by the caller. Ground must be an explicit node; material row-sum defects
    are rejected rather than hidden by reference normalization.

    rim_currents maps names to signed branch currents at that *layer's* rim:
    sheet plus every adjacent barrel segment, all using one sign convention.
    Passing no rims means no via checks apply (not that a via was validated).

    Direct-solve rtol is 1e-6; iterative solves use max(1e-6, 10*solver_rtol).
    Absolute floors are 1 nA and 1 nW as specified in the release 0.1 gates.
    """
    v = np.asarray(voltages, dtype=float)
    if v.ndim != 1 or matrix.shape != (len(v), len(v)) or not np.isfinite(v).all():
        raise ValueError("matrix and finite voltage vector must have matching shapes")
    fixed = _indices(fixed_nodes, len(v), "fixed_nodes", 1)
    if len(np.unique(fixed)) != len(fixed):
        raise ValueError("fixed_nodes contains duplicates")
    matrix = sp.csr_matrix(matrix, dtype=float)
    if not np.isfinite(matrix.data).all():
        raise ValueError("matrix must be finite")
    references, labels = _network_references(matrix, fixed)
    relative_v = v-v[references]
    loss = np.asarray([sheet_loss_w, lumped_loss_w], dtype=float)
    if loss.shape != (2,) or not np.isfinite(loss).all() or np.any(loss < 0):
        raise ValueError("losses must be finite nonnegative scalar powers")
    if not np.isfinite(solver_rtol) or solver_rtol < 0:
        raise ValueError("solver_rtol must be finite and nonnegative")
    rtol = max(1e-6, 10 * solver_rtol)
    external = np.zeros(len(v))
    if injected_current is not None:
        prescribed = np.asarray(injected_current, dtype=float)
        if prescribed.shape != v.shape or not np.isfinite(prescribed).all():
            raise ValueError("injected_current must be finite with shape (N,)")
        external[:] = prescribed
    reaction = np.asarray(matrix @ relative_v)
    if not np.isfinite(reaction).all():
        raise ValueError("nodal currents must be finite")
    external[fixed] = reaction[fixed]
    source = float(external[external > 0].sum())
    sink = float(-external[external < 0].sum())
    current_error = abs(source - sink)
    current_limit = 1e-9 + rtol * source
    component_source = np.bincount(labels, weights=np.maximum(external, 0))
    component_sink = np.bincount(labels, weights=np.maximum(-external, 0))
    component_error = np.abs(component_source-component_sink)
    component_limit = 1e-9 + rtol*component_source
    components = [dict(source_a=float(src), sink_a=float(snk),
                       residual_a=float(err), tolerance_a=float(limit),
                       passed=bool(err <= limit))
                  for src, snk, err, limit in zip(component_source, component_sink,
                                                 component_error, component_limit)]
    delivered = float(relative_v @ external)
    dissipated = float(loss.sum())
    power_error = abs(delivered - dissipated)
    power_limit = 1e-9 + rtol * abs(delivered)
    rims = {}
    for name, currents in (rim_currents or {}).items():
        currents = np.asarray(currents, dtype=float)
        if currents.ndim != 1 or len(currents) < 2 or not np.isfinite(currents).all():
            raise ValueError("each rim needs at least two finite signed branch currents")
        residual = abs(float(currents.sum()))
        limit = 1e-9 + rtol * float(np.abs(currents).sum())
        rims[name] = dict(branch_currents_a=currents.tolist(), residual_a=residual,
                          tolerance_a=limit, passed=bool(residual <= limit))
    current_passed = bool(current_error <= current_limit)
    power_passed = bool(delivered >= 0 and power_error <= power_limit)
    return dict(
        relative_tolerance=rtol,
        current_balance=dict(source_a=source, sink_a=sink, residual_a=current_error,
                             tolerance_a=current_limit, passed=current_passed),
        component_current_balance=components,
        power_balance=dict(delivered_w=delivered, sheet_loss_w=float(loss[0]),
                           lumped_loss_w=float(loss[1]), residual_w=power_error,
                           tolerance_w=power_limit, passed=power_passed),
        via_rims=rims,
        passed=bool(current_passed and power_passed
                    and all(x["passed"] for x in components)
                    and all(x["passed"] for x in rims.values())),
    )

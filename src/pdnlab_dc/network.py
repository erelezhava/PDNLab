"""Compose synthetic DC sheets, terminals and branches. SI units throughout.

Node selections or explicit area-contact polygons are supplied by the caller;
this module does not remesh contact boundaries or read boards. Terminals are
disjoint selections on a sheet.
Without weights a selection is equipotential. Explicit weights instead leave
the selected nodes untied and prescribe the fraction of load current at each.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from time import perf_counter

import numpy as np
import scipy.sparse as sp

from .checks import balance_checks, resistor_losses, sheet_losses
from .contacts import AreaContact, select_area_contact
from .fem import _indices, _network_references, add_resistors, assemble_sheet, tie_nodes
from .solver import PreparationStats, prepare_fixed_voltages
from .vias import barrel_resistance


def _name(name, existing):
    if not isinstance(name, str) or not name.strip() or name in existing:
        raise ValueError(f"name must be a nonempty unique string: {name!r}")


def _scalar(value, name):
    value = np.asarray(value, dtype=float)
    if value.ndim != 0 or not np.isfinite(value):
        raise ValueError(f"{name} must be a finite scalar")
    return float(value)


def _snapshot(values, dtype):
    result = np.array(values, dtype=dtype, copy=True)
    result.flags.writeable = False
    return result


@dataclass(frozen=True)
class _Sheet:
    nodes: np.ndarray
    triangles: np.ndarray
    conductance: np.ndarray
    matrix: sp.csr_matrix
    assembly_s: float


@dataclass(frozen=True)
class _Terminal:
    sheet: str
    nodes: np.ndarray
    weights: np.ndarray | None
    rim: bool
    contact: AreaContact | None = None
    contact_selection_s: float = 0.


@dataclass(frozen=True)
class _Branch:
    a: str
    b: str
    resistance: float


@dataclass(frozen=True)
class NetworkPreparationStats:
    sheet_assembly_s: float
    composition_s: float
    geometric_nodes: int
    solver: PreparationStats
    contact_selection_s: float = 0.


@dataclass
class NetworkResult:
    """One load case; arrays/dicts are caller-owned and do not alter the model.

    Terminal voltages follow each selection's original node order (identical
    entries for an equipotential). Terminal current is external current into
    copper, positive for a supplying source and negative for a sink. Branch
    current is positive from its declared a terminal to b. Checks determine
    passed; a failed conservation check is returned, never silently ignored.
    contact_voltages_v gives electrode voltage or area-average copper voltage
    for explicit area contacts; terminal_voltages_v retains all support values.
    """

    voltages_v: np.ndarray
    sheet_voltages_v: dict[str, np.ndarray]
    terminal_voltages_v: dict[str, np.ndarray]
    terminal_currents_a: dict[str, float]
    branch_currents_a: dict[str, float]
    sheet_losses_w: dict[str, float]
    branch_losses_w: dict[str, float]
    checks: dict
    timings_s: dict[str, float]
    contact_voltages_v: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self):
        return self.checks["passed"]


class Network:
    """Mutable builder; prepare(source_names) creates an independent snapshot.

    Overlapping terminal selections are rejected. Cross-layer via rims remain
    separate terminals joined by finite barrel resistors, never one tied node.
    Floating copper is rejected, as in the low-level solver; board-specific
    floating-island classification belongs to a later preprocessing stage.
    """

    def __init__(self):
        self._sheets = {}
        self._terminals = {}
        self._branches = {}
        self._used = {}

    def add_sheet(self, name, nodes, triangles, sheet_conductance):
        _name(name, self._sheets)
        started = perf_counter()
        # Validate before integer conversion can mask malformed connectivity.
        matrix = assemble_sheet(nodes, triangles, sheet_conductance)
        self._sheets[name] = _Sheet(
            _snapshot(nodes, float), _snapshot(triangles, np.int64),
            _snapshot(np.broadcast_to(sheet_conductance, (len(triangles),)), float),
            matrix, perf_counter()-started)
        self._used[name] = np.zeros(matrix.shape[0], dtype=bool)

    def add_terminal(self, name, sheet, nodes, *, weights=None, rim=False):
        """Select mesh nodes. weights=None ties them; weights must otherwise sum to 1.

        Weights are nonnegative and explicitly ordered like nodes. A bare
        single-vertex terminal remains a mathematical point, with mesh-dependent
        local voltage; no physical contact accuracy is implied by this API.
        A rim must be equipotential and have an incident branch at preparation.
        """
        _name(name, self._terminals)
        if sheet not in self._sheets:
            raise ValueError(f"unknown sheet: {sheet!r}")
        indices = _indices(nodes, len(self._sheets[sheet].nodes), "terminal nodes", 1)
        if len(np.unique(indices)) != len(indices) or np.any(self._used[sheet][indices]):
            raise ValueError("terminal selections must be disjoint and contain no duplicates")
        if not isinstance(rim, (bool, np.bool_)) or (rim and weights is not None):
            raise ValueError("rim must be boolean; rim terminals must be equipotential")
        if weights is not None:
            weights = np.asarray(weights, dtype=float)
            if (weights.shape != indices.shape or not np.isfinite(weights).all()
                    or np.any(weights < 0) or not np.isclose(weights.sum(), 1., rtol=0, atol=1e-12)):
                raise ValueError("weights must be finite nonnegative fractions, one per node, summing to 1")
            weights = _snapshot(weights/weights.sum(), float)
        self._terminals[name] = _Terminal(sheet, _snapshot(indices, np.int64), weights, bool(rim))
        self._used[sheet][indices] = True

    def add_contact(self, name, sheet, region, *, model="equipotential"):
        """Add a finite-area terminal at an explicit polygon, without remeshing.

        Equipotential electrodes require mesh-conforming boundaries. Uniform-
        current patches integrate area weights and are supported as loads only.
        Their support nodes must not overlap another terminal selection; refine
        if disjoint physical contacts share coarse FEM support vertices.
        """
        _name(name, self._terminals)
        started = perf_counter()
        if sheet not in self._sheets:
            raise ValueError(f"unknown sheet: {sheet!r}")
        mesh = self._sheets[sheet]
        selection = select_area_contact(mesh.nodes, mesh.triangles, region, model=model)
        self.add_terminal(name, sheet, selection.nodes, weights=selection.weights)
        self._terminals[name] = replace(self._terminals[name], contact=selection,
                                        contact_selection_s=perf_counter()-started)

    def _add_branch(self, name, a, b, resistance):
        _name(name, self._branches)
        if a not in self._terminals or b not in self._terminals:
            raise ValueError("branch endpoints must name existing terminals")
        if a == b or any(self._terminals[t].weights is not None for t in (a, b)):
            raise ValueError("branch endpoints must be distinct equipotential terminals")
        resistance = _scalar(resistance, "resistance")
        if resistance <= 0 or not np.isfinite(1./resistance):
            raise ValueError("resistance must be positive with representable conductance")
        self._branches[name] = _Branch(a, b, resistance)

    def add_resistor(self, name, a, b, resistance):
        """Add a positive lumped resistor; current is reported a -> b."""
        self._add_branch(name, a, b, resistance)

    def add_barrel(self, name, a, b, *, length, inner_radius, outer_radius, resistivity,
                   straight=0.0):
        """Join distinct sheet rims using the validated axial barrel formula.

        length is layer-centre separation. The caller supplies the two contacted
        layers/radii; this module does not infer a stackup or hole semantics.
        """
        if (a not in self._terminals or b not in self._terminals
                or not self._terminals[a].rim or not self._terminals[b].rim
                or self._terminals[a].sheet == self._terminals[b].sheet):
            raise ValueError("barrel endpoints must be rim terminals on distinct sheets")
        self._add_branch(name, a, b, barrel_resistance(
            length, inner_radius, outer_radius, resistivity, straight))

    def prepare(self, source_terminals):
        """Freeze composition and factor once. Source membership is fixed thereafter."""
        return PreparedNetwork(self._sheets, self._terminals, self._branches, source_terminals)


class PreparedNetwork:
    """Prepared snapshot. Source voltages and sink currents may vary per solve.

    stats separates sheet assembly, area-contact selection, composition and
    low-level preparation/LU.
    Low-level sparse-byte counts exclude the retained original sheet matrices,
    geometry/maps and this wrapper's check matrix; they are not whole-model RSS.
    No transfer matrix is generated unless transfer_resistance is requested.
    """

    def __init__(self, sheets, terminals, branches, source_terminals):
        started = perf_counter()
        self._sheets, self._terminals, self._branches = dict(sheets), dict(terminals), dict(branches)
        if isinstance(source_terminals, str):
            raise ValueError("source_terminals must be a nonempty sequence of names")
        self._sources = tuple(source_terminals)
        if not sheets or not self._sources or len(set(self._sources)) != len(self._sources):
            raise ValueError("require sheets and nonempty unique source terminals")
        if any(t not in terminals or terminals[t].weights is not None for t in self._sources):
            raise ValueError("sources must name existing equipotential terminals")
        offsets, offset = {}, 0
        for name, sheet in sheets.items():
            offsets[name] = offset
            offset += len(sheet.nodes)
        selections = {name: term.nodes+offsets[term.sheet] for name, term in terminals.items()}
        matrix = sp.block_diag([s.matrix for s in sheets.values()], format="csr")
        matrix, mapping = tie_nodes(matrix, [selections[name] for name, term in terminals.items()
                                            if term.weights is None])
        self._maps = {name: mapping[start:start+len(sheets[name].nodes)].copy()
                      for name, start in offsets.items()}
        self._term_maps = {name: mapping[selection] for name, selection in selections.items()}
        self._pairs = np.array([(self._term_maps[b.a][0], self._term_maps[b.b][0])
                                for b in branches.values()], dtype=int).reshape(-1, 2)
        self._resistances = np.array([b.resistance for b in branches.values()])
        if branches:
            matrix = add_resistors(matrix, self._pairs, self._resistances)
        self._incident = {name: [] for name, term in terminals.items() if term.rim}
        for i, branch in enumerate(branches.values()):
            for terminal, sign in ((branch.a, 1), (branch.b, -1)):
                if terminal in self._incident:
                    self._incident[terminal].append((i, sign))
        if any(not incident for incident in self._incident.values()):
            raise ValueError("each declared rim needs an incident branch")
        self._matrix = matrix
        self._fixed = np.array([self._term_maps[name][0] for name in self._sources])
        self._solver = prepare_fixed_voltages(matrix, self._fixed)
        self._references, _ = _network_references(matrix, self._fixed)
        composition_s = perf_counter()-started-self._solver.stats.preparation_s
        self.stats = NetworkPreparationStats(sum(s.assembly_s for s in sheets.values()),
                                             composition_s, offset, self._solver.stats,
                                             sum(t.contact_selection_s for t in terminals.values()))

    @property
    def node_map(self):
        """Copies mapping each sheet's original nodes to electrical unknowns."""
        return {name: indices.copy() for name, indices in self._maps.items()}

    @property
    def last_solve(self):
        """Low-level timing/RHS statistics for the last successful solve or transfer."""
        return self._solver.last_solve

    @property
    def contact_info(self):
        """Caller-owned geometry/model summaries for prepared area contacts."""
        return {name: dict(model=t.contact.model, bounds_m=t.contact.bounds_m,
                           requested_area_m2=t.contact.requested_area_m2,
                           covered_area_m2=t.contact.covered_area_m2,
                           support_nodes=len(t.contact.nodes))
                for name, t in self._terminals.items() if t.contact is not None}

    def solve(self, source_voltages, loads=None):
        """Return one checked case. loads maps terminal names to nonnegative sink A.

        source_voltages must specify exactly the prepared source names. Loads
        on fixed sources are rejected, so no requested current is ignored.
        Unspecified load terminals draw zero. Untied selections use their
        explicit weights; tied selections share current through the solve.
        """
        started = perf_counter()
        if not isinstance(source_voltages, Mapping) or set(source_voltages) != set(self._sources):
            raise ValueError("source_voltages must specify exactly the prepared sources")
        values = [_scalar(source_voltages[name], "source voltage") for name in self._sources]
        loads = {} if loads is None else loads
        if not isinstance(loads, Mapping):
            raise ValueError("loads must map terminal names to sink currents")
        current = np.zeros(self._matrix.shape[0])
        for name, amps in loads.items():
            if name not in self._terminals or name in self._sources:
                raise ValueError(f"load must name a non-source terminal: {name!r}")
            amps = _scalar(amps, "load current")
            if amps < 0:
                raise ValueError("load currents must be nonnegative sinks")
            term = self._terminals[name]
            if term.weights is None:
                current[self._term_maps[name][0]] -= amps
            else:
                np.add.at(current, self._term_maps[name], -amps*term.weights)
        voltage = self._solver.solve(values, current)
        solve_s = self._solver.last_solve.elapsed_s
        post_started = perf_counter()
        relative = voltage-voltage[self._references]
        external = current.copy()
        external[self._fixed] = (self._matrix @ relative)[self._fixed]
        sheet_v, sheet_heat, sheet_current = {}, {}, {}
        for name, sheet in self._sheets.items():
            sheet_v[name] = voltage[self._maps[name]]
            sheet_heat[name] = float(sheet_losses(sheet.nodes, sheet.triangles,
                                                 sheet.conductance, sheet_v[name]).sum())
            sheet_current[name] = sheet.matrix @ relative[self._maps[name]]
        branch_i = ((relative[self._pairs[:, 0]]-relative[self._pairs[:, 1]])/self._resistances
                    if self._branches else np.array([]))
        heat = (resistor_losses(voltage, self._pairs, self._resistances)
                if self._branches else np.array([]))
        rim_currents = {}
        for name, incident in self._incident.items():
            term = self._terminals[name]
            rim_currents[name] = [float(sheet_current[term.sheet][term.nodes].sum()),
                                  *(float(sign*branch_i[i]) for i, sign in incident),
                                  -float(external[self._term_maps[name][0]])]
        checks = balance_checks(self._matrix, voltage, self._fixed, sum(sheet_heat.values()),
                                float(heat.sum()), current, rim_currents)
        contact_v = {
            name: float(voltage[self._term_maps[name][0]]) if term.weights is None
            else float(term.weights @ voltage[self._term_maps[name]])
            for name, term in self._terminals.items() if term.contact is not None}
        return NetworkResult(
            voltage, sheet_v, {name: voltage[m] for name, m in self._term_maps.items()},
            {name: float(external[np.unique(m)].sum()) for name, m in self._term_maps.items()},
            dict(zip(self._branches, map(float, branch_i))), sheet_heat,
            dict(zip(self._branches, map(float, heat))), checks,
            dict(linear_solve=solve_s, postprocess=perf_counter()-post_started,
                 total=perf_counter()-started),
            contact_voltages_v=contact_v)

    def transfer_resistance(self, injection_terminals, observation_terminals=None, *, batch_size=32):
        """Optional dense Z in supplied name order, using bounded RHS batches.

        This initial wrapper supports equipotential terminals only; an untied
        selection has multiple voltages and no single terminal impedance.
        """
        def indices(names, inject=False):
            if isinstance(names, str):
                raise ValueError("terminals must be a nonempty sequence of names")
            names = list(names)
            if not names or any(name not in self._terminals or self._terminals[name].weights is not None
                                or (inject and name in self._sources) for name in names):
                raise ValueError("transfer terminals must be equipotential; injections cannot be fixed sources")
            return [self._term_maps[name][0] for name in names]
        injection = indices(injection_terminals, inject=True)
        observation = injection if observation_terminals is None else indices(observation_terminals)
        return self._solver.transfer_resistance(injection, observation, batch_size=batch_size)

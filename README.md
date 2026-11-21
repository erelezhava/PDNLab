# PDNLab DC

Open-source DC power-integrity (IR drop) solver for PCBs. Own solver code, KiCad 10 boards as the first input, Apache-2.0.

**Status: Phase 1 solver core accepted by Erekle on 2026-11-15.**
Merged through [PR #8](https://github.com/erelezhava/PDNLab/pull/8) with
[passing CI on main](https://github.com/erelezhava/PDNLab/actions/runs/43740365125).
Experiment scripts solve one real-board net. Phase 1 has reusable sparse-LU solves,
checked named-network composition and finite-area contact selection on meshes.
IPC-2581 and per-pin terminal-model choices are confirmed by the maintainer;
the production importer and automatic contact-boundary remeshing are not implemented.
See [the result log](docs/DECISIONS.md) for measured results and limitations,
and the [dependency inventory](docs/dependency-license-audit.md) for the
current licences and packaging boundaries. Production board integration remains
Phase 2 work; Phase 1 acceptance covers the documented synthetic solver scope.

See the [development plan](docs/project-plan.md) for scope, architecture,
experiments and release gates.

## Setup

```sh
uv sync          # creates .venv with numpy, scipy, meshio and pytest
uv run pytest    # runs the analytic tests
```

Without uv: `pip install -e . pytest`, then `pytest`.

Experiments 2 to 4 also need the external Gmsh executable on `PATH`. On Ubuntu:

```sh
sudo apt install gmsh
gmsh --version
```

Without Gmsh, the integration tests that need it are skipped. CI installs it so
those tests run there too. The solver never imports the Gmsh Python API.

## Prepared solver (phase 1)

Existing assembly, ties and resistor primitives remain in `pdnlab_dc.fem`;
barrel resistance is in `pdnlab_dc.vias`. Prepare the final electrical matrix
after tying nodes and stamping resistors:

```python
from pdnlab_dc.solver import prepare_fixed_voltages

system = prepare_fixed_voltages(matrix, fixed_nodes)
v = system.solve(3.3, injected_current)  # positive into copper; sinks negative
v_next = system.solve(3.3, next_current)  # reuses the same factorization
# Optional: observed voltage per +1 A, with fixed voltages at zero:
z = system.transfer_resistance(load_nodes, batch_size=8)
print(system.stats, system.last_solve)
```

Preparation copies the matrix and fixed indices. Reprepare if conductances or
fixed-node membership change; current and fixed-voltage values may change
without preparation. The one-shot `fem.solve_fixed_voltages` remains compatible
and delegates to this solver. Every component still needs a fixed node.
Sources here are voltages to a reference, not arbitrary two-terminal sources.
The input is a conservative nodal matrix, with ground represented explicitly;
material nonzero row sums (including implicit ground shunts) are rejected.
Each connected network is solved relative to one of its fixed voltages, then
shifted back, reducing sensitivity to the rail's absolute voltage.

Transfer extraction bounds full-field RHS/solution columns by `batch_size`,
but its returned dense observation-by-injection matrix can still be large.
It is never built by `solve`. A selected electrical node may represent a tied
finite contact; selecting a bare mesh vertex does not validate point-contact
physics. Explicit area contacts are available through the network interface below.

`system.stats` reports preparation/factorization time, nonzero counts and
sparse-array bytes; it does not measure peak RSS or native LU workspace.
`system.last_solve` records elapsed time and RHS batching. Independent loss
integration and conservation checks remain in `pdnlab_dc.checks` and require
original sheet geometry and branch data from the caller.
Checks normalize reactions and terminal power to the same per-network origin.
They require both global and per-network current balance; independent loss
integration and per-via checks retain their existing tolerances.

Run the synthetic performance probe (no private boards):

```sh
uv run python experiments/phase1_solver_benchmark.py
```

The primary probe solves relative to zero. A separate 3.3 V probe now gates
reference invariance and conservation. The formerly failing power-check case
and its correction are recorded in the result log. Very large common-mode
voltages relative to the drop remain limited by floating-point resolution.
The real-board experiment's transfer/scenario workflow has not been migrated.

## Named synthetic networks

`pdnlab_dc.network.Network` composes the validated primitives without manual
sparse-matrix wiring. All geometry/material inputs use SI units:

```python
from pdnlab_dc.network import Network
from pdnlab_dc.simple_meshes import rectangle_mesh

nodes, triangles, left, right = rectangle_mesh(0.05, 0.005, 12, 4)
net = Network()
net.add_sheet("top", nodes, triangles, sheet_conductance=35e-6 / 1.724e-8)
net.add_terminal("source", "top", left)
net.add_terminal("load", "top", right)
prepared = net.prepare(["source"])
result = prepared.solve({"source": 3.3}, {"load": 0.1})
assert result.passed, result.checks
next_case = prepared.solve({"source": 3.3}, {"load": 0.2})  # same LU
```

Terminal selections are disjoint node groups on one sheet. By default their
nodes are tied equipotential, representing an ideal soldered pad/rim. Passing
explicit `weights=[...]` leaves them untied and allocates sink current by those
nonnegative fractions, in selected-node order. There is no implicit equal
allocation for an untied group. Use `add_contact` below for explicit finite-area
polygons; a bare vertex still has point-contact mesh dependence.

Use `add_resistor(name, a, b, resistance)` between equipotential terminals.
Use `add_barrel(name, a, b, length=..., inner_radius=..., outer_radius=...,
resistivity=...)` between terminals declared with `rim=True` on distinct
sheets. Rims stay separate electrical unknowns; each declared rim must have
an incident branch. The caller supplies contacted layers and copper radii.

Preparation snapshots the builder. Source membership then stays fixed;
`solve` requires exactly those source-voltage names and accepts nonnegative
sink currents by load-terminal name. Unspecified loads draw zero. Unknown
loads, loads at fixed sources, overlapping selections and floating components
raise errors instead of silently changing a case. Source terminal currents
may be positive (supplying) or negative (absorbing).

Results include electrical and per-sheet voltages, per-selected-node terminal
voltages, external terminal currents (positive into copper), branch currents
(declared a → b), independently integrated sheet/branch losses, timings and
conservation checks. Rim KCL includes sheet, every incident branch and any
external current at that rim. Check `result.passed`: failed checks are returned
with their residuals and tolerances. `prepared.node_map` gives copies mapping
original sheet nodes to electrical unknowns.

`prepared.stats` separates sheet assembly, area-contact selection, composition
and solver preparation;
`prepared.last_solve` exposes the last low-level solve/transfer timing and RHS
statistics. Its solver sparse-byte count excludes the wrapper's check matrix,
original sheet matrices, geometry and mappings; it is not whole-model memory.
`prepared.transfer_resistance(["load"], batch_size=8)` optionally extracts Z
in requested name order for equipotential terminals only.

Run the three-layer analytic example with named pads/rims and two barrel
segments, at two mesh resolutions and two current levels:

```sh
uv run python experiments/phase1_network_cases.py
```

## Finite-area contacts

`Network.add_contact(name, sheet, region, model=...)` selects an explicit
Shapely `Polygon` in mesh coordinates (metres), including its position and area:

```python
from shapely.geometry import box

# For the strip above: feed ends on the first grid line; touch may cut cells.
contact_net = Network()
contact_net.add_sheet("top", nodes, triangles, 35e-6 / 1.724e-8)
contact_net.add_contact("feed", "top", box(0, 0, 0.05 / 12, 0.005))
contact_net.add_contact("touch", "top", box(0.025, 0, 0.035, 0.005),
                        model="uniform_current")
contact_model = contact_net.prepare(["feed"])
case = contact_model.solve({"feed": 3.3}, {"touch": 0.1})
assert case.passed, case.checks
print(case.contact_voltages_v["touch"])  # area-average copper voltage
```

- `equipotential` (default) is an ideal electrode shorting only its specified
  area. Its boundary must conform to the existing mesh: materially cut
  triangles raise instead of silently changing the electrode's size. It can
  be a fixed-voltage source or a current sink; the solve determines sharing.
- `uniform_current` prescribes constant current per area without tying the
  copper. Triangle intersections are integrated to obtain FEM nodal load
  weights and the area-average voltage. It supports current sinks, including
  patches whose edges cut triangles. Its support vertices may lie outside the
  polygon; the integrated patch itself is not enlarged. It is not a fixed-
  voltage source or an equipotential terminal for transfer extraction.

These are distinct idealizations of contact behaviour. No contact resistivity,
pressure-dependent distribution or 3D interface model is inferred. The region
must be a valid positive-area 2D polygon fully covered by nonoverlapping copper
elements. Clipping outside copper, nearest-node fallback and zero-area point
regions are rejected. Shared terminal support is still unsupported: refine if
two disjoint physical contacts share coarse-mesh vertices. Automatic remeshing
and pad/importer integration remain later work.

`prepared.contact_info` reports model, bounds, requested/covered area and support
size. `result.contact_voltages_v` reports the electrode voltage or uniform-area
mean; `terminal_voltages_v` still returns all selected nodal voltages. The
standalone `pdnlab_dc.contacts.select_area_contact` exposes the selection and
weights for other callers. Geometry checks use a 1e-10 relative area tolerance;
electrical conservation and analytic tolerances are unchanged.

Run finite-electrode and uniform-patch convergence against analytic references,
with a separate mathematical-point diagnostic:

```sh
uv run python experiments/phase1_contact_cases.py
```

The diagnostic point's voltage grows with refinement; it is not reported as a
physical contact-resistance prediction. The finite circular electrode uses a
fixed 128-sided polygon, so its circle-reference error includes polygon geometry.

## Phase 2 board-model scaffold

`pdnlab_dc.board` contains immutable neutral records for source provenance,
layers, nets, copper features, physical pin pads and drill features. `BoardModel`
validates IDs, references, SI dimensions, polygon topology and layer/drill
ordering. It does not parse XML, transform/repair geometry or infer electrical
contacts. See the [development contract](docs/phase2-board-model-contract.md)
and `tests/test_board.py` for synthetic examples. Production importing remains
unimplemented.

`pdnlab_dc.ipc2581_metadata.read_ipc2581_metadata(path)` reads only the header
and stackup of an IPC-2581 revision C file in millimetres: the input hash,
exporter version when declared, and ordered copper layers with centre Z and
thickness in metres. It returns `Ipc2581Metadata`, not a board: no nets,
tracks, pads, zones or drills are read, and unsupported revisions, units or
stackups raise. It has been tested on synthetic XML only.

`pdnlab_dc.ipc2581_copper.read_ipc2581_copper(path)` adds nets, filled-zone
contours and tracks. It returns `Ipc2581Copper`, again not a board: `Net` records
from the export's `Set` net names; one `CopperFeature` of kind `fill` per zone
contour in metres with its holes; one of kind `track` per straight or arc track,
outlined with round ends at a 1 µm chord error (`pdnlab_dc.track_outline`);
one of kind `pad` per component pad and `via` per via on every copper layer, as
solid outlines from the export's shape dictionary (`pdnlab_dc.pad_outline`; drill
holes are not cut yet); and the non-copper layers it did not inspect. Any other
copper (teardrops, copper text and graphics, footprint-owned zones) raises, so
copper on a net is never silently left out. KiCad's fractured fills are split
back into outline and holes at their zero-width slits; any other unsupported or
invalid geometry raises. Tracks are checked against KiCad's own board file on the
public board in `tests/fixtures/arc_track`; pads and vias against KiCad's own SVG
plot of the public board in `tests/fixtures/pad_shapes`.

## Phase 0 experiments

| # | Experiment | Status |
| --- | --- | --- |
| 1 | Plain rectangle on a hand-made mesh against R = ρL/(Wt) | Passes, see `tests/test_exp01_rectangle.py` |
| 2 | Rectangle through external Gmsh, MSH 4.1 and named boundary tags | Passes, see `tests/test_exp02_gmsh_rectangle.py` |
| 3 | Annular ring with a tied inner rim and mesh refinement | Passes, see `tests/test_exp03_annular_ring.py` |
| 4 | Two layer rings connected by a finite via barrel resistor | Passes, see `tests/test_exp04_two_rings.py` |
| 5 | Current conservation, per-layer via KCL and independent power balance across experiments 1–4 | Passes, see `tests/test_exp05_balances.py` |
| 6 | KiCad 10 IPC-2581 export against saved native board data | Export coverage audits and a real-net reader/solve comparison recorded; IPC-2581 confirmed by the maintainer. One board/net is not general importer validation. |
| 7 | padne real-net reference comparison | Reference run recorded; raw drop discrepancy traced to terminal modelling. KiPI dropped by the maintainer. See [results](docs/DECISIONS.md). |
| 8 | Real net through geometry cleanup, Gmsh and DC solve | Completed experimentally, including multilayer vias and mesh-convergence checks; direct and IPC-2581 readers compared. See [results](docs/DECISIONS.md). |

An extra regression test, `tests/test_offcentre_via.py`, checks a via placed
off-centre in a circular sheet, where current crowds onto one side of the rim.
Its closed-form reference is derived in
[docs/off-centre-via-reference.md](docs/off-centre-via-reference.md).

Print the numbers with `uv run python experiments/exp01_rectangle.py`.

Run the Gmsh round trip with:

```sh
uv run python experiments/exp02_gmsh_rectangle.py
# Keep .geo files, .msh files and results.json for inspection:
uv run python experiments/exp02_gmsh_rectangle.py --output-dir /tmp/pdnlab-exp02
```

Results and recommendations are recorded in [docs/DECISIONS.md](docs/DECISIONS.md).

Run the ring experiment with:

```sh
uv run python experiments/exp03_annular_ring.py
# Keep both mesh levels and their measured results:
uv run python experiments/exp03_annular_ring.py --output-dir /tmp/pdnlab-exp03
```

Run the two-layer via experiment with:

```sh
uv run python experiments/exp04_two_rings.py
# Keep each layer's geometry, mesh and combined results:
uv run python experiments/exp04_two_rings.py --output-dir /tmp/pdnlab-exp04
```

Run all four experiments with the conservation gates:

```sh
uv run python experiments/exp05_balances.py
# Keep the individual results and a combined summary:
uv run python experiments/exp05_balances.py --output-dir /tmp/pdnlab-exp05
```

Each experiment now includes current and power checks in its pass/fail result.
JSON outputs include residuals, tolerances and individual via-rim results where
applicable. Sheet heat is integrated from triangle voltage gradients; resistor
heat is computed from branch voltage drops.

Run the export inspection with KiCad 10 installed:

```sh
# Uses the installed KiCad StickHub demo without modifying it:
uv run python experiments/exp06_kicad_export.py --output-dir /tmp/pdnlab-exp06
# Or audit your own saved board:
uv run python experiments/exp06_kicad_export.py --board /path/to/board.kicad_pcb --output-dir /tmp/pdnlab-exp06-custom
```

The export audit compares saved fills, stackup, pin/net mappings and via holes.
An additional geometry audit checks tracks, pad copper and pad holes; use
`--strict` to make it gate the exit code. Unsupported geometry fails that
audit. The experimental IPC-2581 reader also compares one net's geometry and
solved drops against the direct board reader. This is not a production importer
or validation of Altium/OrCAD X exports. Refill and save your board first; the
audit cannot establish whether cached fills are current. Plating thickness
remains an explicit setup input.

The KiCad integration test is optional and skips without KiCad 10 and the
installed StickHub demo. CI currently installs Gmsh, but does not install KiCad;
the XML inspection unit tests still run there.

Experiment 7 recorded a padne comparison on the private real-board net. The
raw drops disagree because the terminal models differ; downstream comparisons
and remaining limitations are recorded in [DECISIONS.md](docs/DECISIONS.md).
KiPI was dropped from the current plan. The original StickHub reference run
was not performed. Historical setup instructions remain in
[experiment 7 setup](docs/experiment-07-setup.md).

## Layout

| Path | Content |
| --- | --- |
| `docs/project-plan.md` | Development plan, milestones and validation criteria |
| `docs/DECISIONS.md` | Experiment results and recommendations |
| `docs/off-centre-via-reference.md` | Derivation of the off-centre via reference |
| `docs/experiment-07-setup.md` | Reference comparison setup and historical installation instructions |
| `src/pdnlab_dc/` | Solver package |
| `tests/` | Analytic tests, run on every commit |
| `experiments/` | Phase 0 experiment scripts, one per experiment |

## Licence

Apache-2.0, see [LICENSE](LICENSE). Gmsh and KiCad are used only as external programs and are not part of this repository.

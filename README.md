# PDNLab DC

Open-source DC power-integrity (IR drop) solver for PCBs. Own solver code, KiCad 10 boards as the first input, Apache-2.0.

**Status: phase 0 merged through PR #7; phase 1 solver core started.** Experiment
scripts solve one real-board net. The first phase-1 increment adds reusable
sparse-LU preparation and direct load-case solves on synthetic networks.
IPC-2581 and per-pin terminal-model choices are confirmed by the maintainer;
the production importer and contact model are not implemented yet.
See [the result log](docs/DECISIONS.md) for measured results and limitations,
and the [dependency inventory](docs/dependency-license-audit.md) for the
current licences and packaging boundaries. Phase 1 is not complete.

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
physics. No contact geometry builder is implemented yet.

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

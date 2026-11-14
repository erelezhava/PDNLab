# PDNLab DC

Open-source DC power-integrity (IR drop) solver for PCBs. Own solver code, KiCad 10 boards as the first input, Apache-2.0.

**Status: phase 0, experiments.** Nothing here solves a real board yet.

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

## Phase 0 experiments

| # | Experiment | Status |
| --- | --- | --- |
| 1 | Plain rectangle on a hand-made mesh against R = ρL/(Wt) | Passes, see `tests/test_exp01_rectangle.py` |
| 2 | Rectangle through external Gmsh, MSH 4.1 and named boundary tags | Passes, see `tests/test_exp02_gmsh_rectangle.py` |
| 3 | Annular ring with a tied inner rim and mesh refinement | Passes, see `tests/test_exp03_annular_ring.py` |
| 4 | Two layer rings connected by a finite via barrel resistor | Passes, see `tests/test_exp04_two_rings.py` |
| 5 | Current conservation, per-layer via KCL and independent power balance across experiments 1–4 | Passes, see `tests/test_exp05_balances.py` |

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

## Layout

| Path | Content |
| --- | --- |
| `docs/project-plan.md` | Development plan, milestones and validation criteria |
| `docs/DECISIONS.md` | Experiment results and recommendations |
| `src/pdnlab_dc/` | Solver package |
| `tests/` | Analytic tests, run on every commit |
| `experiments/` | Phase 0 experiment scripts, one per experiment |

## Licence

Apache-2.0, see [LICENSE](LICENSE). Gmsh and KiCad are used only as external programs and are not part of this repository.

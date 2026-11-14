# PDNLab DC

Open-source DC power-integrity (IR drop) solver for PCBs. Own solver code, KiCad 10 boards as the first input, Apache-2.0.

**Status: phase 0, experiments.** Nothing here solves a real board yet.

## Setup

```sh
uv sync          # creates .venv with numpy, scipy and pytest
uv run pytest    # runs the analytic tests
```

Without uv: `pip install -e . pytest`, then `pytest`.

## Layout

| Path | Content |
| --- | --- |
| `src/pdnlab_dc/` | Solver package |
| `tests/` | Analytic tests, run on every commit |
| `experiments/` | Phase 0 experiment scripts, one per experiment |

## Licence

Apache-2.0, see [LICENSE](LICENSE). Gmsh and KiCad are used only as external programs and are not part of this repository.

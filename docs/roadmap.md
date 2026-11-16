# PDNLab DC roadmap

Status overview: what is done, what is in progress, what is left. Details and
measured results live in [DECISIONS.md](DECISIONS.md); the full plan, gates and
estimates in [project-plan.md](project-plan.md). Update this file after each
increment, in the same commit or pull request.

Last updated: 2026-11-16, after the nets and zone-fill reader was merged and read
one real KiCad 10 export.

## Where we are

Phase 2 (real boards). Importer steps 1 to 3 are merged; tracks are next. The solver core is accepted
on synthetic cases; the IPC-2581 importer is being built one element type at a
time. Nothing yet reads a full board, meshes it and solves it outside the
experiments.

Committed scope is the MVP: a command-line DC drop solver for KiCad 10 boards,
results viewed in ParaView, release 0.1. Phases 3 to 6 are backlog, re-decided
after release 0.1.

## Phases

| Phase | Status | Gate |
| --- | --- | --- |
| 0 Experiments | Done 2026-11-15 (PR #7) | All 8 experiments passed or understood; IPC-2581 chosen |
| 1 Solver core | Accepted 2026-11-15 (PR #8, #9) | Analytic tests within 0.5% in CI |
| 2 Real boards | **In progress** | Release 0.1: the six gates below |
| 3 Checks and report | Backlog | Release 0.2 |
| 4 Electro-thermal | Backlog | Release 0.3 |
| 5 Viewer | Backlog | Release 0.4 |
| 6 Validation | Backlog | Release 1.0 |

## Phase 2 increments

| # | Increment | Status |
| --- | --- | --- |
| 1 | Neutral board records and validation (`board.py`) | Merged, PR #10 |
| 2 | IPC-2581 header and stackup metadata (`ipc2581_metadata.py`) | Merged, PR #11; read on one real KiCad 10 export (logged, PR #12) |
| 3 | Nets and filled-zone contours (`ipc2581_fills.py`) | Merged, PR #13; real export read: 141 nets, 11 fills, 183 holes |
| 4 | Tracks (segments and arcs) | Next. Decided: 1 µm chord error, vertices on the circle, one feature per track, arcs supported |
| 5 | Pads | Planned |
| 6 | Drills and vias | Planned |
| 7 | Assemble a full `BoardModel` from the readers | Planned |
| 8 | Copper cleanup per net and layer (union of fills, tracks, pads) | Planned |
| 9 | Meshing real copper with Gmsh | Planned |
| 10 | Via and pin contact model on real boards | Planned |
| 11 | Setup file: sources, loads, rails, material and plating | Planned |
| 12 | Solve, VTU output for ParaView, CLI | Planned |
| 13 | Release 0.1 gate run on the real test board | Planned |

Increments 7 to 13 are an outline, not yet agreed in detail; each is split and
decided when it starts.

## Release 0.1 gates

| Gate | Status |
| --- | --- |
| 1 Analytic tests | Pass on synthetic cases (Phase 1) |
| 2 Current conservation, global | Checks exist; not yet run on an imported board |
| 3 Current conservation, per via | Checks exist; not yet run on an imported board |
| 4 Power balance | Checks exist; not yet run on an imported board |
| 5 Mesh convergence on the real board | Shown in experiment 8 only, not on the production path |
| 6 Cross-check with padne | Reference run recorded in experiment 7; production comparison open |

## Open decisions

- Arc direction: needs a small public KiCad 10 board with one arc track, because
  the real test board has no arcs on copper.
- Pads, drills: decided when those increments start.

## Done decisions (Phase 2)

- Z = 0 at the outer face of the first copper layer; IPC Y reversed once; mm
  to m at the reader boundary; copper is `CONDUCTOR` only for now.
- Copper on a net to be simulated must be included; copper text, graphics and
  teardrops raise; footprint-owned zones raise.
- KiCad fractured fills are split at their zero-width slits (confirmed on one
  real export: 183 holes recovered).
- Tracks: 1 µm maximum chord error for all round geometry, vertices on the true
  circle, one feature per segment or arc, arcs supported.

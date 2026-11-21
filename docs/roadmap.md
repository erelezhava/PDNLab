# PDNLab DC roadmap

Status overview: what is done, what is in progress, what is left. Details and
measured results live in [DECISIONS.md](DECISIONS.md); the full plan, gates and
estimates in [project-plan.md](project-plan.md). Update this file after each
increment, in the same commit or pull request.

Last updated: 2026-11-21, after the pad and via reader was checked against
KiCad's own plot on the public pad board and one real board.

## Where we are

Phase 2 (real boards). Importer steps 1 to 4 are merged; step 5 (pads and via
copper) is implemented and awaiting merge; drills are next. The solver core is accepted
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
| 3 | Nets and filled-zone contours (now in `ipc2581_copper.py`) | Merged, PR #13; real export read: 141 nets, 11 fills, 183 holes |
| 4 | Tracks, segments and arcs (`track_outline.py`, `ipc2581_copper.py`) | Merged, PR #16; public arc board 13/13 and real board 785/785 tracks match KiCad |
| 5 | Pads and via copper (`pad_outline.py`, `ipc2581_copper.py`) | Branch `phase2-ipc2581-pads`; public pad board 36/36 and real board 1,206/1,206 pad and via shapes match KiCad's plot |
| 6 | Drills: holes cut from pads and vias, plated barrels joining layers | Next |
| 7 | Thermal reliefs: public test board, fills meeting pads checked against KiCad's plot | Planned (Erekle, 2026-11-21) |
| 8 | Assemble a full `BoardModel` from the readers | Planned |
| 9 | Copper cleanup per net and layer (union of fills, tracks, pads) | Planned |
| 10 | Meshing real copper with Gmsh | Planned |
| 11 | Via and pin contact model on real boards | Planned |
| 12 | Setup file: sources, loads, rails, material and plating | Planned |
| 13 | Solve, VTU output for ParaView, CLI | Planned |
| 14 | Release 0.1 gate run on the real test board | Planned |

Increments 8 to 14 are an outline, not yet agreed in detail; each is split and
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

- Drills: decided when that increment starts (hole shapes, plating, how a barrel
  joins layers).
- Custom pads: whether KiCad writes their outline with Y already flipped is
  accepted from KiCad's source, not yet shown on an export with an asymmetric
  custom pad. Revisit if a real board uses custom pads (the test board has none).

## Done decisions (Phase 2)

- Z = 0 at the outer face of the first copper layer; IPC Y reversed once; mm
  to m at the reader boundary; copper is `CONDUCTOR` only for now.
- Copper on a net to be simulated must be included; copper text, graphics and
  teardrops raise; footprint-owned zones raise.
- KiCad fractured fills are split at their zero-width slits (confirmed on one
  real export: 183 holes recovered).
- Tracks: 1 µm maximum chord error for all round geometry, vertices on the true
  circle (an arc's inner edge may gain up to 1 µm; total area always slightly
  less), one feature per segment or arc, arcs supported; arc direction confirmed
  on the public arc board (`tests/fixtures/arc_track`).
- One copper reader: `ipc2581_fills` renamed `ipc2581_copper`; later element
  types extend the same walk.
- Pads: all six KiCad shapes (circle, rectangle, oval, round-rect, chamfered,
  custom/trapezoid); rotation counter-clockwise in IPC coordinates (confirmed
  against KiCad's plot); component pads and vias read as solid outlines on every
  copper layer, holes cut later; IDs `pad:<layer>:<component>.<pin>` and
  `via:<layer>:<k>`; three KiCad exporter limits documented, not detectable.

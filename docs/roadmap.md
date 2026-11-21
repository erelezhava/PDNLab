# PDNLab DC roadmap

Status overview: what is done, what is in progress, what is left. Details and
measured results live in [DECISIONS.md](DECISIONS.md); the full plan, gates and
estimates in [project-plan.md](project-plan.md). Update this file after each
increment, in the same commit or pull request.

Last updated: 2026-11-21, after the drill reader was merged (PR #19).

## Where we are

Phase 2 (real boards). Importer steps 1 to 6 are merged: every kind of copper
and hole in a KiCad 10 IPC-2581 export is read and checked against KiCad. The
thermal-relief board is next. The solver core is accepted on synthetic cases.
Nothing yet assembles a full board, meshes it and solves it outside the
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
| 5 | Pads and via copper (`pad_outline.py`, `ipc2581_copper.py`) | Merged, PR #18; public pad board 36/36 and real board 1,206/1,206 pad and via shapes match KiCad's plot |
| 6 | Drill holes: `DrillFeature` records linked to pad and via copper (`ipc2581_copper.py`) | Merged, PR #19; public pad board 5/5 and real board 218/218 holes match KiCad's file. Holes are recorded, not cut (cutting and barrels in 9 and 11) |
| 7 | Thermal reliefs: public test board, fills meeting pads checked against KiCad's plot | Next (Erekle, 2026-11-21) |
| 8 | Assemble a full `BoardModel` from the readers | Planned |
| 9 | Copper cleanup per net and layer (union of fills, tracks, pads; holes cut at the setup's radius) | Planned |
| 10 | Meshing real copper with Gmsh (start from experiment 8's settings) | Planned |
| 11 | Via and pin contact model on real boards (rims and barrels, setup plating) | Planned |
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

- Hole cutting radius and plating thickness (setup file, cleanup and contact
  model): whether KiCad's drill value is the drilled or the finished size, and
  the plating, ideally from the fab's specification. Experiment 8 cut at drill
  radius + plating with 25 µm plating; for a 0.2 mm via that choice visibly
  changes edge current density.
- Mesh sizes (meshing increment): start from experiment 8 (h = 0.5 mm, 96-sided
  holes, growth 0.15 mm per mm from holes and through-hole pads; refinement also
  needed on thermal spokes) and settle by release gate 5.
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
- Drills: one `DrillFeature` per hole (`drill:<name>`, kind via, pin or
  mechanical, span from the drill layer), linked by `drill_id` to the pad and
  via copper of the same padstack and net at exactly its position on each span
  layer; recorded, not cut; slots, backdrills and tolerances raise.

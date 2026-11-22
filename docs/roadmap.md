# PDNLab DC roadmap

Status overview: what is done, what is in progress, what is left. Details and
measured results live in [DECISIONS.md](DECISIONS.md); the full plan, gates and
estimates in [project-plan.md](project-plan.md). Update this file after each
increment, in the same commit or pull request.

Last updated: 2026-11-22, after the setup file and output decisions.

## Where we are

Phase 2 (real boards). Increments 1 to 11 are merged: every kind of copper and
hole in a KiCad 10 IPC-2581 export is read, checked against KiCad, assembled
into one `BoardModel`, cleaned into pieces per net and layer with holes cut,
meshed per net and built into a solvable network with barrels, pin contacts and
an island check (private test board: a power net passes the island check and a
pin-to-pin resistance converges within gate 5). The setup file is next: nothing
yet reads sources and loads from a file or writes results.

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
| 7 | Thermal reliefs: public test board, fills meeting pads checked against KiCad's plot | Merged, PR #21; no reader change; public thermal board 12/12 pad and via connections and all fills match KiCad |
| 8 | Assemble a full `BoardModel` from the readers (`ipc2581_board.py`, `PinPad` records) | Merged, PR #23; public boards 26/26 and private board 355/355 pins match KiCad's board file |
| 9 | Copper cleanup per net and layer (`copper_cleanup.py`: pieces with IDs; plated holes cut at finished radius + plating; overlapping nets raise) | Merged, PR #27; public boards and private board (610 pieces) match KiCad's plot piece for piece |
| 10 | Meshing one net with Gmsh (`board_mesh.py`): default size rules, refinement at holes, SMD pads and thin copper; halving the scale halves every size | Merged, PR #30; first mesh within 1% of converged on every case measured, private power-net plane included |
| 11 | Contact model (`board_network.py`): sheets, barrels, equipotential pin contacts, island check; mesh follows pad outlines | Merged, PR #32; exact pin, barrel and ring case within 0.019%; private power net passes the island check |
| 12 | Setup file (TOML): sources and loads by pin groups (power and GND pins), plating, optional resistivity and tolerance; power and GND both simulated | Next: decisions logged 2026-11-22 |
| 13 | Solve with the refinement check; voltage and current-density maps (VTU and PNG), text/CSV summary (per-load voltage, per-pin and per-via currents, hot spots, losses); CLI | Planned |
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
| 5 Mesh convergence on the real board | Shown on the production path for one power net (plane +0.38%, pin to pin +0.33% at the first halving); the per-run check comes in increment 13 |
| 6 Cross-check with padne | Reference run recorded in experiment 7; production comparison open |

## After release 0.1

- Remote sense: sense nets and their position on a source (Erekle, 2026-11-22).

## Open decisions

- Meshing time on dense nets (increment 13): a private power net takes 42 s per
  mesh at scale 1, because refinement zones around 30 holes and many pads cover
  the plane. If it matters, refine only around pads that are sources or loads.
- Custom pads: whether KiCad writes their outline with Y already flipped is
  accepted from KiCad's source, not yet shown on an export with an asymmetric
  custom pad. Revisit if a real board uses custom pads (the test board has none).
- Copper pads on non-plated holes (a copper ring around a mounting hole): raise
  for now (2026-11-21); decide when a real board has one.

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
- Thermal reliefs: read as part of the zone fill, no relief metadata needed;
  spokes and solid connections overlap their pads (public board
  `tests/fixtures/thermal_relief`).
- Board assembly: one `PinPad` per physical pad (one pin's copper at one
  position, all layers); component, pin and position from the copper reader;
  copper pads on non-plated holes raise for now; unread copper raises.
- Holes: KiCad's hole value is the finished size (KLC F7.6, KiCad #7044); plated
  holes are cut at finished radius + plating, the barrel added by the contact
  model; plating thickness is a required setup input, no default; non-plated
  holes cut at their radius.
- Cleanup (2026-11-22): overlapping copper of two nets raises (net ties too, for
  now); copper with no net kept as its own group outside the DC solve; each net
  and layer split into pieces with IDs; an island on a simulated net is an error
  (checked in increment 11, once barrels join layers).
- Cleanup output: pieces `piece:<layer>:<net>:<k>`, largest first; hole outlines
  at the 1 µm rule; breakout and pieces under 1 µm² raise; track stubs inside a
  drilled hole are listed as drilled away (37 on the private board).
- Meshing (2026-11-22): the user does not choose element sizes. Default rules
  from experiment 8 plus refinement at thin copper; every run halves the sizes
  and compares (tolerance a user setting, default gate 5), with a round limit
  and an error if it does not settle. The loop is built in increment 13.
- Meshing defaults (measured 2026-11-22): maximum 0.5 mm, growth 0.075 per mm
  from holes and SMD pads, a quarter of the local width in thin copper; sizes
  not spread from the outline (experiment 8's setting made its meshes finer
  than stated).
- Contact model (2026-11-22): resistivity default 1.724e-8 Ω·m (planes and
  barrels separately overridable); pads equipotential, the mesh follows their
  outlines; through-hole pins contacted on their component's side; an island on
  a simulated net raises and nothing is simulated; `fem.tie_nodes` fix for large
  tied pads (approved).
- Setup and output (2026-11-22): TOML; sources and loads by equipotential pin
  groups, nets found from the pins; power and GND solved as separate networks
  with the same currents; maps as VTU and PNG plus a text/CSV summary; remote
  sense after release 0.1.

# PDNLab DC: Experiment results and recommendations

This log records measured results separately from the proposed targets in the
[development plan](project-plan.md). Dates refer to the local development run;
CI results are tracked separately by GitHub Actions.

Each entry ends its result line with a **recommendation** from whoever ran the
experiment. A recommendation is not a project decision: whether to continue,
change approach or stop after an experiment is decided by the maintainer.

## 2026-11-14 — Experiment 1: hand-made rectangle

**Result: passed. Recommendation: continue to experiment 2.**

- Reference: R = ρL/(Wt), using ρ = 1.724e-8 Ω·m at 20 °C.
- Resistance relative-error limit: 1e-6; nodal voltage absolute-error limit:
  1e-6 V with terminals at 1 V and 0 V.
- All 11 original tests passed, including reversed triangle orientation,
  alternating diagonals, jittered interior nodes and zero-area rejection.
- The standalone script's six cases had a maximum relative resistance error
  of approximately 1.2e-11.

This validates the uniform-field assembly case. It does not establish accuracy
for current crowding or real boards.

## 2026-11-14 — Solver input validation

The review reproduced negative resistance for negative conductance, nonfinite
voltages from an unanchored island, and misleading resistance from empty
terminals. These cases now raise `ValueError`.

The assembler also rejects nonfinite coordinates, malformed triangle arrays,
fractional indices and indices outside the mesh. The solver checks finite
voltages/currents, current-vector shape and component anchoring; singular-solve
warnings become errors. A current-injection test verifies the strip's voltage
field and nodal current balance.

**Decision:** low-level solves reject unanchored components. The future board
preprocessor will classify and report floating copper as specified in the plan;
that preprocessing is not implemented yet.

## 2026-11-14 — Experiment 2: external Gmsh round trip

**Result: passed. Recommendation: continue to experiment 3.**

Route exercised: `.geo` file → external `gmsh` process → ASCII MSH 4.1 → meshio
→ physical surface and boundary tags → FEM assembly → terminal reaction current.

Environment: Gmsh `4.14.0-git`, meshio `5.3.5`, Python `3.14.4`.
The executable is installed separately; the solver does not import Gmsh.

| Case | Length (mm) | Width (mm) | Copper (µm) | Mesh size (mm) | Nodes | Resistance (Ω) | Relative error |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Coarse | 50 | 5 | 35 | 5 | 32 | 4.925714286e-3 | 7.40e-15 |
| Fine | 50 | 5 | 35 | 1 | 360 | 4.925714286e-3 | 2.66e-14 |
| Thin trace | 100 | 0.2 | 18 | 0.2 | 1084 | 4.788888889e-1 | 1.09e-12 |
| Wide | 2 | 80 | 70 | 1 | 246 | 6.157142857e-6 | 0 within floating-point precision |

Every case meets the resistance limit of 1e-6 relative and reproduces the linear
voltage field within 1e-6 V.

Physical groups are `left` (101), `right` (202), and `copper` (303); their tags
deliberately differ from geometry entity IDs. Boundary nodes are selected by
tags. Tests independently compare those sets with the expected edge coordinates.
A separate fixture checks unused-node compaction and missing-group rejection.

Reproduce with:

```sh
uv run pytest -q
uv run python experiments/exp02_gmsh_rectangle.py --output-dir /tmp/pdnlab-exp02
```

The script writes geometry, mesh and `results.json` with Gmsh version, dimensions,
material values, node/triangle counts, resistance and voltage errors. Without
`--output-dir`, it uses a temporary directory and removes it afterward.

**Verification:** 36 tests passed locally, with no skips. CI now installs Gmsh;
the updated CI workflow has not yet been run remotely.

**Limits:** this adapter handles a planar linear-triangle sheet at z = 0 and
named line boundaries. KiCad ingestion, geometry cleanup, annular rim ties,
multilayer vias and real-board solving remain unimplemented.

## 2026-11-14 — Experiment 3: annular ring and equipotential rim

**Result: passed. Recommendation: continue to experiment 4.**

Reference: R = ρ·ln(b/a)/(2πt), with inner radius a, outer radius b and copper
thickness t. The inner rim is at 1 V and the outer rim at 0 V. The two pass
conditions are resistance error ≤ 0.5% and decreasing error when the target
element size is halved. All three geometries satisfy both conditions, on both
mesh levels.

| Case | a (mm) | b (mm) | Copper (µm) | Coarse → fine size (mm) | Coarse → fine nodes | Coarse error | Fine error |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Ring | 1 | 5 | 35 | 0.25 → 0.125 | 1668 → 5937 | 0.01084% | 0.002000% |
| Via scale | 0.15 | 1.5 | 18 | 0.05 → 0.025 | 3601 → 14049 | 0.01827% | 0.002526% |
| Narrow | 3 | 6 | 70 | 0.4 → 0.2 | 734 → 2750 | 0.004926% | 0.0001126% |

Environment: Gmsh `4.14.0-git`, meshio `5.3.5`, Python `3.14.4`.

Implementation and independent checks:

- The geometry uses four CAD arcs per circle, with all four arcs collected by
  each named physical rim group. First-order mesh edges are straight chords;
  refinement reduces both geometry and FEM approximation errors.
- `tie_nodes()` merges the inner-rim electrical unknowns using
  G_reduced = Pᵀ G P. It returns a map for restoring original nodal voltages.
  All inner-rim voltages are identical by construction. The mesh geometry is
  retained for interpreting results.
- The logarithmic radial voltage field is checked independently, with maximum
  nodal voltage error below 0.005 V for a 1 V excitation. The largest observed
  error is about 0.001794 V on the coarse via-scale mesh, decreasing to
  0.000386 V after refinement.
- Solving with individual fixed inner-rim voltages gives the same resistance
  and field as the electrically merged rim.
- With the merged inner rim free and driven by 1 A instead, its solved voltage
  equals the resistance times 1 A. This exercises the tie as a free electrical
  unknown, rather than only as a prescribed-voltage boundary.
- A separate two-branch resistor test checks current sharing, summed terminal
  currents and preservation of dissipated power by node merging. Separate tie
  groups remain electrically distinct; overlaps, duplicates and invalid node
  indices are rejected.

Reproduce with:

```sh
uv run pytest -q
uv run python experiments/exp03_annular_ring.py --output-dir /tmp/pdnlab-exp03
```

The script saves both `.geo`/`.msh` levels plus `results.json`, including Gmsh
version, dimensions, material, geometric/electrical node counts, resistance
errors and voltage-field errors. Without `--output-dir`, files are temporary.

**Verification:** 54 tests passed locally, with no skips or warnings. The CI
workflow has not yet been run remotely.

**Limits:** this validates a single conducting sheet and a tied circular rim.
It does not yet validate a barrel resistor between distinct layer rims,
via current branching, KiCad import or measured hardware. Resistance errors
include both polygonal-boundary and finite-element effects; their small values
alone do not establish accuracy for general board geometry.

## 2026-11-14 — Experiment 4: two layer rims and a finite barrel

**Result: passed. Recommendation: continue to experiment 5.**

Two annular sheets are meshed separately. Their conductance matrices form a
block-diagonal network. Each inner rim is tied independently, then a lumped
resistor connects those two distinct electrical nodes. The top outer edge is
held at 1 V and the bottom outer edge at 0 V.

Reference: R_total = R_ring_top + R_barrel + R_ring_bottom, with
R_ring = ρ·ln(b/r_o)/(2πt) and R_barrel = ρL/[π(r_o² − r_i²)]. The finished-hole
radius is r_i, r_o = r_i + plating, and each sheet hole uses r_o. L is the
layer-centre separation. All three cases use ρ = 1.724e-8 Ω·m.

| Case | Finished hole (mm) | Plating (µm) | Layer spacing (mm) | Top / bottom outer radii (mm) | Top / bottom copper (µm) | Solved total (mΩ) | Total error |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Via scale | 0.3 | 25 | 1.6 | 1.5 / 1.5 | 35 / 35 | 1.417465 | 0.0026% |
| Unequal | 0.3 | 25 | 0.05 | 1 / 2 | 18 / 70 | 0.394911 | 0.0098% |
| Sheet dominated | 2 | 100 | 0.005 | 5 / 3 | 18 / 35 | 0.309594 | 0.0011% |

The pass gate is ≤ 0.5% relative resistance error. The 5 µm layer spacing in
the sheet-dominated case is a synthetic numerical stress test, not a proposed
PCB stackup. It makes a shorted barrel difficult to detect from total resistance
alone, so the barrel voltage drop is checked separately against its analytic
share of the 1 V excitation. All cases also meet a 0.5% limit on that drop.

Implementation and independent checks:

- `barrel_resistance()` computes axial resistance from copper radii and length.
  Geometry and material inputs must be finite and positive, with r_o > r_i.
- `add_resistors()` stamps positive lumped resistances into a new matrix,
  preserving the original. Unit tests cover series and parallel branches,
  reversed terminals, symmetry, row sums and invalid inputs.
- Separate layer rim nodes have a nonzero voltage difference. A single shared
  node would bypass the barrel and is not used.
- Each layer's voltage drop is checked against its own ring reference, so a
  barrel-dominated case cannot hide a missing or inaccurate sheet contribution.
- The assembled resistance also agrees with the sum of the two separately
  solved discrete ring resistances and the barrel to 1e-9 relative.
- Barrel current agrees with terminal current, and the sheet currents at the
  two rims have opposite signs and matching magnitudes. The next experiment
  will generalise conservation and power checks across all four experiments.
- Doubling the barrel resistance increases the total by one original barrel
  resistance. Both layer rims remain equipotential internally.

Environment: Gmsh `4.14.0-git`, meshio `5.3.5`, Python `3.14.4`.

Reproduce with:

```sh
uv run pytest -q
uv run python experiments/exp04_two_rings.py --output-dir /tmp/pdnlab-exp04
```

The script saves each layer's geometry and mesh plus a combined `results.json`
with geometry, material, Gmsh versions, rim node IDs, component references,
total resistance, barrel current and barrel voltage drop. Files are temporary
when `--output-dir` is omitted.

**Verification:** 77 tests passed locally, with no skips or warnings. The CI
workflow has not yet been run remotely.

**Limits:** this validates assembly of two ideal sheets joined by one uniform
barrel segment. It does not establish the accuracy of that simplified model
against a real plated via or full 3D solution. Intermediate-layer branching,
contact resistance, thermal effects and KiCad ingestion remain future work.

## 2026-11-14 — Experiment 5: conservation and independent power balance

**Result: passed. Recommendation: continue to experiment 6.**

Conservation checks now run inside experiments 1 to 4, affect their exit status,
and are included in saved JSON. The experiment 5 runner invokes all four public
scripts and writes a combined summary. It covers 19 solves and six separate
via-rim checks (top and bottom for each of the three two-layer cases).

The checks implement the release 0.1 tolerances:

- Global current: abs(source − sink) ≤ 1 nA + 1e-6 × source current.
- Each via rim: abs(sum of signed branch currents) ≤ 1 nA + 1e-6 × sum of
  absolute branch currents at that layer node.
- Power: abs(net power delivered − sheet loss − lumped loss) ≤ 1 nW + 1e-6 ×
  net power delivered.
- For a future iterative solve, each relative factor becomes
  max(1e-6, 10 × solver relative tolerance). The present experiments use direct
  solves and the base 1e-6 factor.

| Experiment | Solves | Maximum global current residual (A) | Maximum power residual (W) | Maximum via-rim residual (A) |
| --- | --- | --- | --- | --- |
| 1 Hand-made rectangle | 6 | 7.05e-11 | 5.09e-11 | Not applicable |
| 2 Gmsh rectangle | 4 | 2.91e-11 | 5.37e-12 | Not applicable |
| 3 Annular ring | 6 | 4.37e-11 | 2.91e-11 | Not applicable |
| 4 Two rings and barrel | 3 | 1.49e-10 | 5.89e-11 | 2.31e-10 |

Every check passes its own absolute-plus-relative tolerance. These are
numerical 1 V excitations for resistance extraction, not proposed operating
voltages or currents for physical coupons.

Implementation and validation:

- `sheet_losses()` integrates g·area·|grad(v)|² per original triangle using a
  coordinate-gradient solve. It does not use vᵀGv, so the power comparison is
  independent of the matrix assembly. Tied-node voltages are expanded back to
  each original layer mesh before integration.
- `resistor_losses()` computes each branch's (V_a − V_b)²/R. The two-layer
  cases report each sheet's loss and the barrel loss separately.
- `balance_checks()` uses reaction currents only at prescribed-voltage nodes,
  plus explicitly prescribed currents at free nodes. Internal numerical
  residuals are not counted as external terminals. Source and sink current
  magnitudes are summed with their signs accounted for; delivered power is
  the signed sum of terminal v·i, including absorbing current sinks.
- Via KCL uses the sheet-only matrix reaction and separately evaluated barrel
  currents at each layer rim. It does not cancel the two layers together.
  The checker also accepts multiple adjacent segment currents; a synthetic
  branching test verifies this summation, but a real three-layer FEM via has
  not yet been validated.
- Unit tests cover an analytic triangle gradient, per-triangle material values,
  constant-voltage zero loss, resistor loss, current-source power, voltage
  reference shifts, absolute floors near zero and iterative-tolerance scaling.
- Deliberately omitted terminal current, missing barrel heat, and wrong rim
  signs are rejected. In the wrong-sign test, global current and power still
  pass while each layer rim fails, checking that the local gate is effective.

Reproduce with:

```sh
uv run pytest -q
uv run python experiments/exp05_balances.py --output-dir /tmp/pdnlab-exp05
```

The output contains per-experiment directories and a summary `results.json`.
Every check records its residual, tolerance and pass/fail result. Without
`--output-dir`, output files are temporary.

Environment: Gmsh `4.14.0-git`, meshio `5.3.5`, Python `3.14.4`.

**Verification:** 97 tests passed locally, with no skips or warnings. The CI
workflow has not yet been run remotely.

**Limits:** self-consistency catches numerical and bookkeeping faults; it does
not establish physical model accuracy. KiCad export inspection, real-board
meshing, full 3D comparison and hardware measurements remain future work.

## 2026-11-14 — Additional check: off-centre via (current crowding)

**Result: passed. Not one of the eight planned experiments; added during review
of experiments 2 to 5.**

Experiments 3 and 4 are axisymmetric, so the current density is uniform round
the via rim. This check moves the hole off-centre in a circular sheet, which
concentrates the current on the side nearest the edge, and compares against

R = ρ/(2πt) · acosh((a² + b² − d²)/(2ab))

for hole radius a, sheet radius b and centre offset d. The derivation, the
closed-form voltage field and the closed-form share of current leaving through
the near half of the outer edge are in
[off-centre-via-reference.md](off-centre-via-reference.md).

| Case | a (mm) | b (mm) | d (mm) | Copper (µm) | Coarse → fine size (mm) | Coarse → fine nodes | Coarse error | Fine error | Largest field error (V) | Near-half current: solved / closed form |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Via 0.15 mm from edge | 0.175 | 1.5 | 1.175 | 35 | 0.05 → 0.025 | 3621 → 13363 | 0.0255% | 0.0017% | 0.0014 | 0.93491 / 0.93491 |
| Via half-way out | 0.175 | 1.5 | 0.7 | 35 | 0.05 → 0.025 | 3690 → 13373 | 0.0072% | 0.0010% | 0.0009 | 0.78228 / 0.78229 |
| Large hole | 1 | 5 | 3 | 70 | 0.25 → 0.125 | 1644 → 5912 | 0.0340% | 0.0048% | 0.0018 | 0.86322 / 0.86322 |

Limits applied by the test: resistance error ≤ 0.5% and smaller at h/2; nodal
voltage within 0.005 V of the closed-form field for a 1 V excitation; near-half
current share within 0.002; current and power balance checks pass. The offset
lowers the resistance by 12% to 47% relative to a centred hole, far more than
the tolerance, so a misplaced hole cannot pass.

`write_annulus_geo()` gained an optional `inner_offset`. With the default of 0
it writes the same file as before, which a test checks.

Environment: Gmsh `4.15.2`, meshio `5.3.5`, Python `3.13.18`.

Reproduce with:

```sh
uv run pytest -q tests/test_offcentre_via.py
```

**Limits:** the tied rim is the exact boundary condition for this 2D problem,
so agreement shows the tie, the assembly and the mesh handle a non-uniform rim
current. It does not show that an equipotential rim represents a real plated
barrel; that needs the full-3D coupon or measurement.

## 2026-11-14 — Experiment 6: KiCad 10 IPC-2581 export coverage

**Result: the audited items match on the sampled board. Recommendation
(provisional): keep IPC-2581 as the candidate importer route and continue to
experiment 7. The route is not settled: tracks, pad copper geometry and a
multilayer board have not been audited (see the review notes below).**

No user board was present in the repository. The sample is the real routed
two-layer StickHub demo installed with KiCad, read from
`/usr/share/kicad/demos/stickhub/StickHub.kicad_pcb`. It has 94 footprints, five
zones and an explicit stackup. Its saved file identifies generator version
`9.99`; the export in this experiment was performed by KiCad **10.0.8**. The
source was not modified. No third-party board design has been copied into this
repository.

Source SHA-256:
`cee5a90f8803791cc55498d2576ed1f3416060d37701a02d4ec2937073c76051`.

Command:

```sh
kicad-cli pcb export ipc2581 --units mm --precision 6 --version C --output /tmp/pdnlab-exp06/board.xml /usr/share/kicad/demos/stickhub/StickHub.kicad_pcb
```

The inspection compares actual native-board data against exported values,
rather than checking only whether XML element names exist.

| Required data | Observation | Result |
| --- | --- | --- |
| Zone fills | All 11 saved fill polygons matched exported contour vertex cycles, with their net and copper layer | Pass |
| Stackup | F.Cu and B.Cu each 0.035 mm; dielectric 1.510 mm; both mask layers 0.010 mm; exported values match | Pass |
| Pin-to-net | All 266 unique (reference, pad number, net) mappings match, with no unexpected mappings | Pass |
| Via holes | All 87 vias match by position, diameter, net and start/end layer span; diameters are 0.3 and 0.4 mm | Pass |

Details and implications:

- KiCad's export places pin references under netted copper `Set`/`Pad` elements;
  this file has no `LogicalNet` elements. A future importer must follow the
  observed netted feature structure, not assume a logical-net section exists.
- Saved fill polygons appear as contours on the appropriate copper layer.
  Their cycles match after reversing the exported Y axis, ignoring the start
  vertex and winding direction, and rounding coordinates to a 1 nm grid.
- `dielectric 1` is exported as `DIELECTRIC_1`. The audit normalises that naming
  change when comparing explicit thicknesses.
- Via holes occur on a drill layer with a `Span` from F.Cu to B.Cu. Diameter
  values equal the source board's via hole values. KiCad's
  [10.0 PCB Editor manual](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html)
  identifies the via Hole property as the finished-hole diameter. Therefore
  the planned default interpretation remains finished hole: r_i = d/2 and
  r_o = d/2 + plating.
- **Plating thickness is not established by this audit.** It must remain an
  explicit setup input; the experiment does not validate 25 µm or another
  universal manufacturing default.
- The saved stackup includes soldermask thickness in its 1.6 mm overall
  thickness. Barrel length must use the copper-layer centre positions, not
  the board's overall thickness.

The export command and options are documented in the
[KiCad 10 CLI reference](https://docs.kicad.org/10.0/en/cli/cli.html).

Reproduce with:

```sh
uv run pytest -q
uv run python experiments/exp06_kicad_export.py --output-dir /tmp/pdnlab-exp06
# An explicitly selected board can be inspected instead:
uv run python experiments/exp06_kicad_export.py --board /path/to/board.kicad_pcb --output-dir /tmp/pdnlab-exp06-custom
```

The output includes `board.xml` and `results.json` with source/export hashes,
KiCad version, exact command, source/export thicknesses, coverage counts,
mismatches, scope limits and route decision. The export hash can change between
runs because the XML contains export history metadata.

**Verification:** 118 tests passed locally, with no skips or warnings. Mutation
tests change polygon coordinates, thicknesses, references, pin numbers, nets,
via diameters, positions and spans without changing counts; the corresponding
gates fail. Unsupported revision, units or contour transforms are rejected.
The integration test also checks the source board hash before and after export.
CI has not been run remotely and does not currently install KiCad; the native
export integration test skips there while the inspection unit tests run.

**Limits:** this is a narrow export audit, not a finished importer. It covers
explicit stackups, saved linear fill polygons, netted pad identities and
circular vias. It does not check cached-fill freshness, PTH/NPTH pad holes,
slots, component-pad coordinate transforms, actual intermediate-layer via
contacts, arbitrary net/layer renaming or exporter behaviour on every board.
The next steps must broaden these cases as needed. No fallback parser is
selected on the evidence from this sample, and no real-board DC solve is
claimed.

### Review notes (Claude, 2026-11-14)

The export itself was not re-run in review, because no KiCad 10 was available
there. What was confirmed independently:

- The board hash above equals the hash of `demos/stickhub/StickHub.kicad_pcb`
  at tag `10.0.8` of the KiCad source mirror.
- Running the native-side parser on that file reproduces the source counts: 94
  footprints, 5 zones, 11 fill polygons, 266 pin-to-net mappings, 87 vias.

What limits the conclusion:

- **Tracks are not audited.** The board has 1,113 track segments and 180 track
  arcs. The +5V net alone has 106 segments and 14 arcs, about 80 mm of track,
  against two zone fills. A DC solve of that net depends on the tracks.
- **Pad copper geometry is not audited.** Pin-to-net identity is compared; pad
  shapes, positions and rotations are not.
- **Two copper layers, through vias only.** Nothing here exercises inner
  layers, several dielectrics, or blind and buried via spans. The project
  target is 8 to 12 layers.
- **The board file was saved by a 9.99 development build** (file format
  version 20250907). A board saved by a KiCad 10 release has not been tried.
- **Finished-hole convention.** The manual page cited above could not be opened
  in review. KiCad forum and developer-list posts agree that KiCad's hole value
  is the finished size, so the default stays as planned, but treat the manual
  citation as unverified.

Before the importer route is settled: add tracks (segments and arcs) and pad
geometry to the audit, then run it on a multilayer board saved by KiCad 10.

## 2026-11-14 — Experiment 7: reference-tool preparation and install blocker

**Result: blocked, not passed. Recommendation: install the external reference
tools before attempting a numerical comparison; do not claim reference
voltages.**

The development session has neither a padne executable nor a KiPI checkout.
Attempting `git ls-remote https://github.com/atx/padne.git HEAD` failed with
`Could not resolve host: github.com`. Direct package installation was already
observed to fail DNS resolution in this session. Documentation and source were
read through connected retrieval, but that does not supply executable binaries
or their dependencies to the local environment.

Prepared a reproducible benchmark using the same unmodified StickHub board as
experiment 6. The source and load terminals and all probes are checked against
the saved pad-to-net mappings before preparation. The source-board hash remains
`cee5a90f8803791cc55498d2576ed1f3416060d37701a02d4ec2937073c76051`.

- Source: 5 V at J2.1 relative to J2.4.
- Load: 0.5 A at J7.1 with an ideal common return at J2.4. This deliberately
  excludes remote return-copper drop for an initial single-rail comparison.
- Probes: J2.1 through J8.1, each on +5V.
- Target material: 35 µm copper on each layer, ρ = 1.724e-8 Ω·m at 20 °C,
  25 µm assumed barrel plating, zero etch undercut. Actual tool settings must
  be confirmed before accepting results.
- Thermal, active components and other operating loads are excluded. This is
  a synthetic distribution benchmark, not the board's complete operating model.

`experiments/exp07_reference_tools.py` makes working copies of native project
files, writes a benchmark manifest and external padne directives, and records
missing tools as blockers with null reference voltages. It can attempt padne's
documented solve command once a suitable executable is available. A successful
solution file still requires reviewed probe extraction before the comparison
is complete. Its exit status remains 2 while measurements are pending.

Installation and run instructions are in
[experiment-07-setup.md](experiment-07-setup.md). They use padne's official
all-in-one Linux executable and the official KiPI `v1.0.1` source in a separate
Python 3.12 environment. The unrelated PyPI `kipi` package is not used.
Neither tool is added to PDNLab's dependencies or incorporated into its solver.

Relevant interfaces were checked against the upstream padne CLI and directive
source. In particular, current padne source supports `--directives` and
`--no-schematic-directives`; the downloaded binary must be probed for these
options because it can differ from the source branch. KiPI's standalone entry
point is `kipi.__main__:main`, not its KiCad plugin action entry point.

**Verification:** the preparation command produced benchmark/directive files
and an explicit blocked report. A test checks that missing tools produce exit
2, never a pass or fabricated voltage, and that the working-board and source
hashes remain identical. All 119 tests pass locally, with no skips or warnings.
No external reference solver has been run; no meshing/runtime or numerical
accuracy conclusion can yet be drawn.

### Review notes (Claude, 2026-11-14)

- The directive file and command line were compared with padne at commit
  `acab836` (2026-10-28): `VOLTAGE` (p, n, v), `CURRENT` (f, t, i), `PROBE` (p)
  and `COPPER` (conductivity in S/m, plating and undercut in metres) exist with
  these parameter names, the external file format accepts `#` comment lines,
  and `solve` takes `--directives` and `--no-schematic-directives`. The
  all-in-one binary may be older than that commit, so the preflight still has
  to pass on the downloaded file.
- Nothing was run. The status stays blocked until both tools have been
  installed and tried on the board.

## 2026-11-15 — Experiment 8: one real net through cleanup, Gmsh and the solver

**Result: passed against the plan's criterion (the real copper meshes), and
taken further than planned: the net was also solved, with per-via currents and
a mesh-convergence study. Not compared with another tool or a measurement in
this experiment. Recommendation: treat the script as a prototype of the
pipeline, not as the importer, and settle the importer route next.**

The plan asks for one net and layer of a real board through geometry cleanup
and Gmsh. `experiments/exp08_real_net.py` does that for every layer of one net
and then solves it:

- It reads the saved `.kicad_pcb` file directly, not the IPC-2581 export that
  experiment 6 audited. The provisional importer route was therefore not
  exercised here.
- Per layer, the net's tracks, pads and saved zone fill are merged into
  polygons with Shapely (`make_valid`, union, fragments under 1e-6 mm² dropped).
  Via holes are cut at drill radius plus plating.
- Each layer is meshed by Gmsh as an external program and assembled with the
  existing `pdnlab_dc` sheet code. Every via gets one tied rim per contacted
  layer and one barrel resistor between successive contacted layers. Each pad
  is one equipotential; a through-hole pin is an ideal lead between layers.
- The solve returns a transfer-resistance matrix: Z[p, k] is the drop at pin p
  per ampere drawn at pin k, with the source pins at zero drop.

Case: net +3V3 of the local test board `MAIN_MCU_STM32H743` (four copper
layers; the net has copper on F.Cu, In2.Cu and B.Cu; the board is not in the
repository). Setup values, not measured: ρ = 1.724e-8 Ω·m, 25 µm plating,
copper thickness from the board stackup (35 µm outer, 15.2 µm inner), the
saved zone fill taken as current, 100 mA at each of the 11 IC supply pins
(1.1 A in total). Three alternative supply entries were solved.

| Source pins | Largest drop | Self-resistance at the load pins |
| --- | --- | --- |
| J4.6, J4.10, J4.13, J4.16 | 3.025 mV | 4.44 to 5.85 mΩ |
| J1.1 | 2.681 mV | 4.22 to 5.62 mΩ |
| J5.1 | 2.578 mV | 4.18 to 5.57 mΩ |

These are on the reported mesh: h = 0.5 mm, via holes as 96-sided polygons,
element size growing 0.15 mm per mm away from holes and from through-hole pads
that feed copper. For every source the matrix is symmetric to 2.4e-14
(relative), satisfies Z[p, k] ≤ min(Z[p, p], Z[k, k]), and the source current
equals the 1.1 A load to 1.2e-12 A.

Per-via results for the J4 source (`experiments/exp08_via_currents.py`):

- The net has 28 vias; 16 carry current and 12 carry none.
- The four feed vias next to J4 carry 337.6, 315.7, 276.5 and 170.3 mA, which
  sum to 1.100 A. Eleven vias carry 100 mA each.
- Highest densities are at the 337.6 mA via (0.3 mm drill): 13.2 A/mm² in the
  barrel, 20.2 A/mm² averaged over the In2.Cu hole edge, 38.2 A/mm² peak on
  the B.Cu hole edge. The layer edge, not the barrel, is the smaller
  cross-section there.
- Kirchhoff's current law holds at every via rim to 5e-13 A.

Mesh convergence over six levels (the first run without local refinement;
h = 0.5 mm with 24-, 48- and 96-sided holes; h = 0.25 and 0.125 mm with 96):
42 of 42 gates pass.

- Per-via current changes by at most 0.71 mA between consecutive refined
  levels (on a 315 mA via).
- Largest drop and self-resistance change by at most 0.37 % between
  consecutive refined levels, over all three sources.
- The first run had no local refinement. For J4 its numbers hold within
  0.51 % at every later level (gate: 1 %). For J1 and J5 they were about 1.9 % low (2.630 mV
  against 2.681 mV for J1) and are superseded: those through-hole pads join
  In2.Cu through 0.5 mm thermal-relief spokes that the first mesh covered with
  one or two elements.

**Verification:** 24 tests in three files check the prototype on small
synthetic boards: a strip against ρL/(Wt); one via joining two strips on
different layers against a closed form (within 3e-3 unrefined, 6e-4 at
h = 0.25 mm with 96-sided holes, approached from below); a pad fed through one
thermal-relief spoke against a closed form (more than 2 % low unrefined,
within 3e-3 refined); a three-layer via with two loads; a tampered barrel current
that must fail the conservation check; via-in-pad rejected. The result files
were computed in a session sandbox with Gmsh 4.12.1 on a copy of the board and
written to the board's `_exp08` directory; a second sandbox run on
2026-11-15 reproduced `checks.json` exactly. The maintainer reported 181
tests passed on his machine on 2026-11-15; whether the board solve has been
run there is not recorded.

**Limits:**

- One board, one net. The geometry cleanup met no failure on it, so this says
  little about boards with slivers, notches or unusual pad shapes.
- Every pad is an equipotential. The padne comparison in the next entry puts
  0.2 to 0.3 mV of drop inside the J4 card-edge fingers when the terminal is
  a point at the pad centre; an equipotential pad has none.
- Refinement covers via holes and through-hole pads. SMD pads joined to a
  zone through thermal spokes are not refined; this net has none.
- Only a zone's `thermal_gap` is read, not per-pad or per-footprint relief
  overrides. Oval holes are rejected. Via-in-pad is rejected by the via
  current report. Through-hole pins have no barrel resistance.
- The hole-edge peak density assumes an equipotential rim.
- The load currents are a uniform assumption, not the board's operating loads.
- The script is about 650 lines in `experiments/`, outside the `pdnlab_dc`
  package, and takes its board data from a direct s-expression read.

## 2026-11-15 — Experiment 7: padne reference run on a real net

**Result: padne ran; the raw comparison fails release gate 6 at every pin, and
the difference is located at the source pads. Downstream of the feed vias the
two tools agree within gate 6. KiPI was not run and the StickHub benchmark was
not run. Recommendation: add a terminal model for unsoldered contact pads to
PDNLab, and do not use padne's pad-centre voltages as a ±5 % reference.**

padne's all-in-one Linux executable installed and reports
`padne 0.1.dev1+gacab836b1`, the commit the directives were checked against on
14 November. Its SHA-256 is `6831f466a67c55097c376503d190204610f9ac8498c2d8bce63106a29920d00d`. The bundled KiCad prints
`PROPERTY_ENUM(): No enum choices defined` assertions on every start; the solve
completes after them.

The comparison uses the experiment 8 case instead of the prepared StickHub
benchmark, because PDNLab results and mesh-convergence data already exist for
it: net +3V3 of the local test board `MAIN_MCU_STM32H743` (not in the
repository; board file SHA-256
`d1535d701eec4db47dc4eb7e3bfd08135ed13735c7ec7ec2b5b59c6029d54187`), supply at
J4.6, J4.10, J4.13 and J4.16, 100 mA at each of the 11 IC supply pins.

padne setup (external directives, schematic directives ignored):

- `VOLTAGE v=3.3V p=J4.6,J4.10,J4.13,J4.16 n=J4.1`
- eleven `CURRENT i=100mA f=<load pin> t=J4.1`, so every load returns to the
  source's negative pad and GND copper carries no load current
- `COPPER conductivity=58004640.37 plating=25u undercut=0`, which is
  ρ = 1.724e-8 Ω·m and 25 µm plating, the experiment 8 assumptions
- copper thicknesses from the board stackup (35 µm outer, 15.2 µm inner), as in
  experiment 8
- run 1: default mesh (`--mesh-size 0.6`), 64,558 unknowns; run 2:
  `--mesh-size 0.15`, 283,621 unknowns; other mesh options at their defaults;
  `scipy` solver backend

Voltages were read from `padne paraview` exports at the mesh vertex that padne
seeds at each connected pad centre. PDNLab values are the experiment 8 results
on its refined mesh (h = 0.5 mm, 96-sided holes, growth 0.15).

Drop from the source pads to the load pads, PDNLab minus padne, padne as the
reference. Gate 6 is abs(difference) ≤ 50 µV + 5 % × abs(padne drop).

| | padne 0.6 mm | padne 0.15 mm |
| --- | --- | --- |
| padne drop over the 11 pins | 2.963 to 3.330 mV | 3.037 to 3.411 mV |
| PDNLab drop over the 11 pins | 2.660 to 3.025 mV | same |
| Raw difference | −302 to −347 µV (−9.2 to −11.0 %) | −377 to −444 µV (−11.3 to −13.5 %) |
| Raw gate 6 | 0 of 11 | 0 of 11 |
| In2.Cu at the four feed vias, below the source pads | padne 0.444 mV, PDNLab 0.158 mV | padne 0.477 mV, PDNLab 0.158 mV |
| Difference referenced to that plane voltage | −16 to −61 µV (−0.6 to −2.2 %) | −58 to −125 µV (−2.2 to −4.3 %) |
| Referenced gate 6 | 11 of 11 | 11 of 11 |

**Where the raw difference comes from.** Between each J4 pad and the rim of its
feed via, on the pad's own layer, padne drops 0.27 to 0.39 mV and PDNLab 0.06
to 0.11 mV (default padne mesh). The four differences are 0.21 to 0.28 mV, and
the In2.Cu plane carries a nearly uniform offset of the same size. The two
tools model a terminal differently:

- padne connects a terminal at the pad centre point. The J4 pads are 0.6 × 2 mm
  card-edge fingers with the feed via beyond one end, so the current runs along
  1 mm of finger: 1.67 squares of 35 µm copper, 0.82 mΩ.
- PDNLab ties the whole pad to one voltage, so that length costs nothing.

With the 1.1 A shared equally by the four fingers, the strip estimate is
0.23 mV. padne's per-pad currents were not extracted, so this is a check of
size only.

The plan's reason for an equipotential pad is that solder and the component
lead short it. That does not hold for a card-edge finger, a test point or any
pad that is touched by a contact instead of soldered. For such pads PDNLab's
drop is optimistic; padne's centre point is not exact either, because the real
contact position along the finger is a property of the mating connector.

**padne's result moves with its mesh.** From 0.6 mm to 0.15 mm every pad-centre
drop rose by 74 to 98 µV (2.4 to 3.0 %). Release gate 5 allows 10 µV + 1 % for
one halving of the element size. For the same case PDNLab's largest drop is
3.0245 mV at h = 0.5 mm and 3.0266 mV at h = 0.125 mm (0.07 %), and between
consecutive refined levels it changes by at most 0.13 %, self-impedance by at
most 0.28 % (`_exp08/v3v3_via_checks.json`). A point current source in a sheet
has a voltage that grows with ln(1/h) as the mesh is refined: for a mesh four
times finer the term is I·R_sheet/(2π)·ln 4. That is 30 to 37 µV for the three
B.Cu fingers, which carry 0.28 to 0.34 A in the PDNLab solution, and 18 µV for
the F.Cu finger at 0.17 A. The observed growth on the source side is 34 µV at
the plane reference and 41 to 46 µV at the feed-via rims on the pad layers.
Two mesh sizes cannot show the logarithmic trend itself.

The referenced difference also grew, by 40 to 64 µV. The same point-source
term for a 100 mA load pad is 11 µV, so most of this growth is not explained.
padne also connects each via through 16 rim points per layer; that was not
isolated.

`experiments/exp07_padne_compare.py` reproduces these numbers from the board
file, the experiment 8 output directory and the padne exports. The reference
ring is 0.15 mm wide, starting at the PDNLab hole radius; widths of 0.10 and
0.30 mm move the referenced difference by at most 6 µV. The directive file,
both solve logs, both solutions and the comparison JSON are in the test
board's `_exp07` directory, which is not in the repository.

A fault found on the way: `experiments/exp07_reference_tools.py` wrote
`plating=25um` for the StickHub benchmark. padne's value parser knows the units
A, V and R only, takes the trailing `m` as milli and then fails on `25u`. The
valid spelling is `plating=25u` (metres). The review note of 14 November
checked the parameter names and missed this. The script and its test are
corrected; the StickHub solve itself has still not been attempted.

**Verification:** six new tests cover the export reader (piece joining, sign of
y), the pad-centre lookup, the ring average, the gate and the feed-via choice
on synthetic data. They need no padne, Gmsh or board. The comparison was run
in a session sandbox on copies of the files, where the suite gave 145 passed
and 21 skipped (no Gmsh or KiCad there). It has not been run on the
maintainer's machine since these changes.

**Limits:**

- One board, one net, one load case. Only the J4 source case was compared; the
  J1 and J5 cases, where the source is a through-hole pin, were not. padne
  models a through-hole pad as a via with a barrel, PDNLab as an ideal lead.
- The referenced comparison assumes the two tools split the current between
  the four feed vias similarly. padne's via currents were not extracted.
- Other model differences were not separated: padne cuts each hole at the
  drill radius and PDNLab at drill radius plus plating; padne takes the barrel
  length as the stackup thickness from the bottom of the upper layer to the
  bottom of the lower layer and adds a lateral barrel term; PDNLab uses the
  distance between layer centres.
- padne is not known to be validated against measurement, so agreement
  downstream of the feed vias is a cross-check, not proof of accuracy.
- The PDNLab numbers come from the experiment 8 result files
  (`_exp08/checks.json` and the per-layer fields), described in the entry
  above.

**Open for the maintainer:**

- Terminal model for contact pads: per-pin choice between a soldered pad
  (equipotential) and a contact (a point or small area at a stated position).
- Whether the padne half of experiment 7 is closed with this result, and
  whether KiPI is still wanted.

## 2026-11-15 — Importer route: local IPC-2581 comparison and independent review

**Result: the sandbox comparison was reproduced locally: geometry and all
three comparison gates pass. Recommendation: accept this as evidence for the
KiCad IPC-2581 route; ask the maintainer to confirm the importer route and
terminal model before phase 1. Cross-CAD compatibility remains untested.**

This entry replaces the uncommitted IPC-2581 draft's claimed results with
measurements from this machine. The draft's whole-board strict-audit run was
not repeated here; its reported counts are not adopted as local measurements.
The local and GitHub-main logs contained two entries dated 2026-11-15 before
this append, rather than the three mentioned in the handoff.

`experiments/exp08_ipc2581_net.py` reads net +3V3 from the private test board's
IPC-2581 C export and compares it with `exp08_real_net.read_net`, using the
same cleanup, meshing and FEM code. The private board and generated output
remain git-ignored under `tests/test boards/`.

Inputs measured for this run:

- Board SHA-256:
  `d1535d701eec4db47dc4eb7e3bfd08135ed13735c7ec7ec2b5b59c6029d54187`.
- Export SHA-256 (the export supplied as re-exported on 2026-11-15):
  `0b97c73bdb3ca19ff4046a6f52370fa140f5956ced05f57e0aa4be377b1f8c00`.
- Gmsh reports `4.14.0-git`; Python `3.14.4`.

Geometry comparison:

| Item | Board file | Export | Difference |
| --- | --- | --- | --- |
| Copper layers and stackup | 4 layers | 4 layers | none |
| Track segments | 96 | 96 | none on the 1 nm comparison grid |
| Vias | 28 | 28 | position, drill, pad size and span equal |
| Pads | 41 | 41 | kind, layers and drill equal |
| In2.Cu fill | 1 polygon, 1198.575741213 mm² | 1 polygon | 0 mm² symmetric difference |

Maximum pad-centre difference is 2.84e-14 mm; maximum outline distance
(Hausdorff distance between boundaries) is 2.07e-8 mm. The original
area/perimeter outline statistic is 4.56e-9 mm, but is no longer the gate:
a narrow local outline error can hide in an average-area statistic.

Setup values: 100 mA per load at
`U3.8,Y2.4,U1.B2,U1.D2,U1.F4,U1.F5,U1.F6,U1.H1,U1.K1,U2.8,U4.8`;
ρ = 1.724e-8 Ω·m; plating 0.025 mm; h = 0.5 mm; 96-sided holes;
growth 0.15. Source J4 uses `J4.6,J4.10,J4.13,J4.16`, J1 uses `J1.1`,
and J5 uses `J5.1`.

| Source | Direct largest drop (mV) | Export with relief: maximum relative drop difference | Export alone: largest drop (mV) | Export alone: maximum difference (µV) |
| --- | --- | --- | --- | --- |
| J4 | 3.024526 | 8.85e-14 | 3.024214 | 0.359739 |
| J1 | 2.680877 | 7.75e-14 | 2.678449 | 2.453349 |
| J5 | 2.577941 | 8.37e-14 | 2.574698 | 3.267775 |

Differences are maxima over all load pins, not just the pin with largest drop.
All requested loads are reachable. All three gates pass: geometry within the
stated tolerances, drop agreement within 1e-9 relative with the relief gap
supplied, and 10 µV + 1% without it. The direct and relief-supplied meshes
both have 20,770 F.Cu nodes, 198,167 In2.Cu nodes and 14,677 B.Cu nodes.
Without the gap, In2.Cu has 189,227 nodes.

Against the saved sandbox JSON (reported Gmsh 4.12.1), the local direct drops
differ by at most 0.0008425 µV (J4), 0.004903 µV (J1) and 0.022614 µV (J5).
The sandbox result is therefore reproduced within small mesher-dependent
numerical differences, not bit for bit.

Independent review and corrections:

- Y is reversed for track endpoints, pad centres, holes and fill vertices.
  Rectangular and rounded-rectangle rotations are restricted to multiples of
  90 degrees. Symmetry makes rotation sign irrelevant for these shapes at
  these angles; arbitrary-angle rotation direction remains unvalidated.
- Via size is looked up from circular copper pads by position, separately on
  each copper layer. Missing, inconsistent, duplicate or orphan via pads and
  holes now raise rather than silently changing the model.
- A plated hole identifies a through-hole pin by position, not an assumed
  padstack name. Unmatched holes, multilayer pin pads without a matching hole,
  duplicate holes and unsupported spans now raise.
- Unsupported feature geometry, container transforms, pad scaling, slots,
  unknown plating states and non-round track ends now raise. Existing
  rejection of arcs, custom/mirrored pads and curved contours is retained.
  These checks apply to the selected net's copper and drill content.
- Unreachable or empty load sets raise, rather than allowing a comparison
  restricted to the remaining reachable loads. Output JSON now records input
  hashes and the Gmsh version.
- Experiment 6 now reports the combined coverage and geometry audit in its
  printed and saved recommendation. A passing strict audit no longer says
  that tracks and pads still need auditing. No importer choice is made.

**Verification:** baseline suite: 201 passed, no skips. After review changes:
226 passed, no skips. Tests use the hand-written board/export pair and cover
Y reversal, supported rotations, via-size changes, hole/pad correspondence,
unsupported content, a local outline defect, and combined audit messaging.
The optional KiCad demo integration ran. The requested real-net command was
run before and after the reader corrections; its report is
`tests/test boards/test brd 1/_exp08/ipc2581/ipc2581_vs_direct.json`.

**Limits and pending work:** one board, one net, KiCad export only. Pin electrical
types and thermal-relief gaps remain absent from the reader's export-derived
board dictionary; explicit loads and the two relief-gap cases expose these
limitations. The pad model is still equipotential, and through-hole leads
remain ideal. No Altium/OrCAD X import, new padne solve, KiPI run, hardware
measurement or phase-1 migration was performed.

Local branch switching/commit is blocked because `.git` is read-only in this
session; terminal GitHub access also failed DNS resolution. The GitHub
connector confirmed main commit `9992ccce1eca2e88204989313ef861f844809c14`
has the same tree as the tested local HEAD, but branch creation returned
403 (`Resource not accessible by integration`). No branch was published.
The accidentally included documentation files were left untouched pending
maintainer direction.

## 2026-11-15 — Maintainer approvals and phase-0 closeout

**Result: Erekle confirmed the importer route and terminal-model proposal,
dropped KiPI, and requested removal of the two accidentally committed
documents. Recommendation: complete the remaining phase-0 housekeeping and
dependency licence audit, then begin phase 1; no further reference-tool run
is recommended as a prerequisite.**

Maintainer instructions in this conversation:

- Remove `docs/experiments-review.md` and
  `docs/pdnlab-dc-tool-description.html`. Both were removed from the working
  tree; their Git history is retained.
- Confirm IPC-2581 as the importer route. Reading an Altium or OrCAD X export
  is not a prerequisite to phase 1; compatibility remains untested.
- Approve a per-pin choice between a soldered equipotential pad and a contact
  point/area at an explicit position. This approval selects the model scope;
  it does not validate or implement the contact model. Recommendation: use a
  finite contact area for physical voltage-drop predictions, and treat an
  ideal point's mesh-dependent voltage explicitly.
- Drop the KiPI reference run from the current plan. KiPI was not run.

The working plan, README experiment status and reference-tool setup status
were updated to reflect these instructions. No solver code or private board was changed, and phase 1 was not
started. No tests or numerical solves were rerun for this documentation-only
closeout. The last measured full suite remains 226 passed, no skips.

Remaining closeout recommendations: review/merge the IPC-2581 branch with this
cleanup; complete the dependency licence audit promised in phase 0 (the plan
still labels its licence table as memory-based); record the phase-0 gate as
closed with the padne discrepancy understood and KiPI explicitly removed.
Contact-model implementation and validation belong to phases 1/2, rather than
another phase-0 experiment.

## 2026-11-15 — Phase 0 dependency inventory and transition recommendation

**Result: the dependency inventory and approved documentation cleanup are
complete in the working tree. Recommendation: merge `exp8-ipc2581-route` to
close phase 0, then begin phase 1 on a separate branch. No further phase-0
numerical run is recommended.**

The maintainer requested finishing phase 0 and proceeding to phase 1 after
confirming IPC-2581, approving per-pin terminal models, removing the two
accidentally committed documents, and dropping KiPI.

`docs/dependency-license-audit.md` records 21 installed dependency distributions
and 45 local licence-file SHA-256 hashes. The audit distinguishes Python
project licences from GEOS, bundled numerical runtimes and other notices.
Gmsh reports `4.14.0-git` and KiCad CLI `10.0.8`. Current upstream licensing
information was checked for the external tools and for Shapely/GEOS, Colorama
and Hatchling. No dependency change is recommended for the current source-only
workflow with separately installed dependencies and external executables.

The inventory is scoped: other-platform wheels, uninstalled Python-version
alternatives, isolated build transitives and future bundled application
artifacts were not exhaustively audited. These are recorded packaging/version
review tasks, not evidence of solver failure. No binary bundle is released by
this change, and no categorical legal compatibility guarantee is claimed.

Phase-0 evidence: experiments 1–5 have analytic/conservation checks; experiment
6 and the IPC-2581 real-net comparison cover the selected route; experiment 7
records the padne reference and terminal-model discrepancy, with residual
limitations stated; experiment 8 meshes and solves the real net. KiPI and the
original StickHub reference case were not run. General cross-CAD support, the
finite-area contact implementation, full-3D correlation and measurements are
not claimed complete.

Verification for this closeout: documentation consistency and `git diff --check`; no tests or numerical solves rerun. The last measured full suite is
226 passed, no skips. No source code or private board changed. Phase 1 has
not started; the remaining step is maintainer review/merge of this branch.

## 2026-11-15 — Phase 1 increment 1: reusable prepared sparse solver

**Result / Recommendation: baseline and final regression suites pass; reusable
LU and bounded transfer RHS batches are implemented. The larger absolute-voltage
probe exposes a pre-existing power-check failure. Recommendation: review this
increment, then address common-mode conservation sensitivity before extending
synthetic model composition and finite-area contacts. Phase 1 is not complete.**

Starting state: clean working tree on `exp8-ipc2581-route`, HEAD
`23b7274f6e22a2781cb7e143057d8de209b77786`. GitHub reports PR #7 merged at
`eb052c029fce61ff37e2a320319655593d5646c0`; its tree
`0bd38f00cfc6c0d6e7abefb15b950c8acda2d426` equals the local HEAD tree.
Thus the checkout contains the merged content, but not the merge commit in
its local history. No repository-specific instruction file was found in the
checkout or its checked parent locations; the conversation's rules apply.

`git fetch origin main` and `git switch -c phase1-solver-core` both failed
because `.git` is read-only in this session. The requested local branch could
not be created. Changes remain uncommitted on the existing checkout; nothing
was staged, pushed or merged. Private boards were not modified or staged.

Implementation:

- Keep validated assembly, node ties, resistor stamps, barrel formula and
  independent loss/conservation functions in their existing package modules.
- Add `pdnlab_dc.solver.prepare_fixed_voltages(matrix, fixed_nodes)` returning
  a `PreparedSystem`. Preparation snapshots inputs, checks anchoring and
  factors the free matrix once with sparse LU. `solve` accepts changing fixed
  voltages and signed current vectors, without transfer extraction. A sink is
  negative current. Conductance or fixed-membership changes require reprepare.
- Preserve `fem.solve_fixed_voltages` as a one-shot compatibility wrapper;
  existing experiments exercise the new solver without changing their scripts.
- Make transfer extraction explicit, with bounded RHS/full-field column batches
  and selected observation nodes. The returned dense transfer matrix still
  grows with observations × injections. Direct and transfer calls share LU.
- Report preparation time (including factorization), factorization time, solve
  time, sparse nonzeros/array bytes and maximum RHS batch bytes. Sparse bytes
  count G, fixed coupling and exposed L/U arrays; they exclude opaque native
  storage, permutations, temporary workspace, Python overhead and peak RSS.
  No large-board bottleneck or speedup is established by these probes.

Validation commands used the existing virtual environment directly because
`uv run` could not write its default cache in this session:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python experiments/phase1_solver_benchmark.py
```

Baseline: **226 passed, no skips, 43.44 s**. Final suite: **257 passed, no skips,
43.02 s**. All existing analytic, mesh-convergence and conservation regressions
ran. The new three-layer synthetic strip/barrel case has intermediate-layer
branching, two mesh resolutions and two loads; terminal drops and barrel
currents meet its tighter 1e-6 relative gate. Independent triangle loss,
resistor loss and all three rim KCL checks pass. New contract tests count one
factorization across repeated solves/transfer extraction, verify input snapshot
isolation, direct/transfer equivalence, actual RHS batch shapes, all-fixed and
multiple-fixed cases, and invalid-input rejection. No tolerance was relaxed.

Synthetic probe: 100 × 20 mm, 35 µm copper, rho = 1.724e-8 ohm metre,
source on the left edge at zero reference, 24 mesh-vertex current samples,
eight direct cases (10 to 80 mA per sample), transfer batch size eight.
Each mesh is released before constructing the next. These vertex samples
exercise scaling, not physical contact accuracy. The table is one final local
invocation after pytest ended; it is not a statistical performance benchmark.
Python 3.14.4, NumPy 2.5.4, SciPy 1.18.1.

| Nodes / free | Triangles | Preparation incl. LU (ms) | LU (ms) | Eight direct cases (ms) | 24-column transfer (ms) | Sparse array bytes | Maximum transfer RHS bytes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2,121 / 2,100 | 4,000 | 8.263 | 5.862 | 2.884 | 4.699 | 980,860 | 134,400 |
| 6,601 / 6,560 | 12,800 | 36.721 | 31.689 | 16.391 | 19.224 | 4,592,636 | 419,840 |

Matrix/factor nonzeros: 10,361 / 68,548 and 32,601 / 341,316. The corresponding
single direct RHS arrays use 16,800 and 52,480 bytes. Largest direct-versus-
transfer voltage differences are 8.67e-19 and 2.17e-18 V. Current residuals
are 3.43e-13 and 2.65e-12 A; independently integrated power residuals are
6.97e-16 and 5.18e-15 W. Both zero-reference probes pass their conservation gates.

**Measured limitation:** the initial probe used 3.3 V. On the 6,601-node case,
its current residual is 4.75e-9 A (passes) but its power residual is 1.5654e-8 W,
above the 4.0601e-9 W limit (fails). The maximum voltage difference after
subtracting the 3.3 V shift is only 7.05e-12 V. Running the original HEAD's
`spsolve` implementation independently on that same synthetic case also fails
power: residual 1.3338e-8 W versus limit 4.0601e-9 W. This is not introduced by
the reusable LU API. Common-mode roundoff in reactions and signed terminal
power is the likely explanation; it has not been fully isolated or corrected.
The benchmark reports the 3.3 V diagnostic explicitly, including its failed
gate, alongside the zero-reference primary case. No check is weakened or
silently treated as passing. Recommendation: make reaction/power evaluation
numerically stable under a reference shift, with a regression for this case.

Not run: remote CI, new private-board solves, new padne/KiPI runs, hardware or
full-3D validation, large-board/peak-RSS profiling or an iterative backend.
No production importer, contact-area geometry builder, thermal or viewer work
was performed. The real-board experiment still has its original transfer and
scenario orchestration; migrating it and avoiding simultaneous coarse/fine
model retention there remain follow-up work.

## 2026-11-15 — Phase 1 increment 2: voltage-reference stability

**Result / Recommendation: the previously failing 3.3 V synthetic probe now
passes unchanged current/power gates. Recommendation: review this correction
before continuing synthetic model composition. Phase 1 remains in progress.**

Starting state verified: clean `phase1-solver-core`, HEAD `7a192f3`; Erekle
created the branch and committed the first increment in his terminal. This
entry supersedes the first increment's unresolved common-mode limitation for
the tested cases, while retaining that entry as the original evidence.

Isolation on the 6,601-node synthetic sheet from the preceding entry:

- Maximum absolute matrix row sum: 2.2737e-12 S; total matrix sum:
  1.48975e-9 S. An exact conservative sheet has zero row sums. Multiplying
  their floating-point remainder by the rail voltage creates apparent leakage
  in an absolute-voltage solve/reaction calculation.
- At 80 mA per sample and 3.3 V, the old current residual is 4.7463e-9 A.
  The raw signed-power residual is 1.5654e-8 W. Re-evaluating only the terminal
  power with voltages relative to the source reduces it to 8.7277e-12 W;
  current cancellation still needs correction.
- The old zero-load 3.3 V case has a 4.7476e-9 A current residual, exceeding
  its 1 nA floor. Merely centering the power calculation would not fix this.
  The old 10 µA-per-sample case also fails current at 3.3 V.

Correction:

- Preparation selects one fixed-node reference per connected network.
  `solve` applies fixed voltages relative to it, solves with the existing LU,
  then restores absolute voltages. Changing the reference value still reuses
  the factorization; different fixed voltages within a network remain valid.
- `balance_checks` independently evaluates fixed-node reactions using relative
  voltages, and terminal power using sum((v-v_ref)*external_current). Sheet
  heat still comes from triangle gradients and lumped heat from branch drops.
  No heat is substituted with a matrix quadratic form.
- Global current, power and via-rim gates retain their existing tolerances.
  Each connected network additionally passes its own current gate, exposed as
  `component_current_balance`. This is necessary because net current must be
  zero in each network for its power to be independent of its voltage origin;
  opposite missing currents in separate networks must not cancel globally.
- Before reference normalization, row sums must be zero within
  64 × float64 epsilon × each row's absolute coefficient sum. This structural
  roundoff bound permits FEM assembly/tie accumulation; it does not replace
  or enlarge the current/power gate tolerances. Material defects raise
  `ValueError`. Ground must be represented as an explicit fixed node;
  reduced matrices with implicit ground shunts are outside this interface.
  No matrix entry is modified by the reference validation.
- The benchmark now requires the 3.3 V diagnostic to pass as well as its
  zero-reference primary case, with nonzero exit status on failure.

Measured corrected benchmark (same meshes, material, currents and Python/
NumPy/SciPy versions as the preceding entry):

| Nodes | 3.3 V global current residual (A) | 3.3 V power residual (W) | Power tolerance (W) | Largest difference after subtracting 3.3 V (V) |
| --- | --- | --- | --- | --- |
| 2,121 | 5.1115e-13 | 7.6588e-16 | 4.0555e-9 | 2.2204e-16 |
| 6,601 | 3.7264e-12 | 5.2553e-15 | 4.0601e-9 | 2.2204e-16 |

Both zero-reference and 3.3 V cases pass global current, per-network current
and independent power gates. No vias are present in this performance probe;
via-rim coverage comes from the existing analytic regressions.

The new regression file covers the same sheet at 0, +3.3, -3.3, 12 and 48 V,
each at 80 mA, 10 µA and zero per sample. Drops agree after shifting to within
the asserted 1e-13 V absolute bound. Zero load produces exactly constant
voltages and zero loss/current. Wrong load declarations still fail. Other
cases cover distinct network references, mixed fixed voltages, material
row-sum defects, and opposite component imbalances that would otherwise pass
both global current and normalized power. Before the fix the new regression
file produced 15 failures and 7 passes; afterward all 22 pass.

Verification: final full suite **279 passed, no skips, 53.52 s**. Focused
reference/prepared-solver/conservation/input tests: **91 passed, 1.43 s**.
The synthetic benchmark exited successfully with both voltage references
gating its status. `git diff --check` passes. Reproduce with:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python experiments/phase1_solver_benchmark.py
```

Not run: new private-board solves, remote CI, new reference-tool runs,
hardware/full-3D validation or large-board peak-memory profiling. The original
experiment scripts remain regression evidence. Arbitrarily large common-mode
voltages relative to tiny drops still face floating-point representation
limits; the tested voltage range is not an unlimited precision guarantee.
No importer, contact builder, thermal or viewer changes were made. Work is
uncommitted; no files were staged, pushed or merged by this session.

## 2026-11-15 — Phase 1 increment 3: named synthetic network composition

**Result / Recommendation: named composition passes analytic strip, branching
three-layer barrel and annular-via checks. Recommendation: review this bounded
interface before adding geometry-based finite-area contacts. No phase-1
closeout or production-import readiness is claimed.**

Starting state verified: clean `phase1-solver-core`, HEAD `42adc63`; Erekle
committed the reference-stability correction. Local baseline was rerun:
**279 passed, no skips, 50.78 s**. No additional repository instruction file
was found. The conversation's project rules continue to apply.

`pdnlab_dc.network.Network` composes the existing validated package primitives:

- Named sheet meshes/materials, named disjoint terminal selections and named
  positive lumped branches. Equipotential terminals tie their selected nodes;
  explicit nonnegative node weights instead leave the selection untied and
  allocate current in that exact node order. Untied groups do not receive an
  implicit equal allocation. A bare vertex remains a mathematical point;
  no contact geometry is constructed by this increment.
- Barrel segments use the existing axial formula between separate rim
  terminals on distinct sheets. No common cross-layer rim node is created.
  Each declared rim must have an incident branch. The caller supplies the
  contacted sheets, layer-centre spacing and copper radii.
- Preparation snapshots the builder and fixes the source-terminal membership.
  Repeated cases specify those source voltages and nonnegative named sink
  currents, with no implicit transfer extraction. Unknown/malformed loads,
  loads at fixed sources, overlapping terminal selections, invalid branch
  endpoints and unanchored copper raise errors. No islands are silently dropped.
- Results preserve maps from original sheet nodes to electrical unknowns and
  report electrical/sheet/terminal voltages, signed external terminal currents,
  a-to-b branch currents, independently integrated sheet/branch loss, solve/
  postprocessing timing and conservation checks. Rim KCL includes sheet current,
  every adjacent branch, and any external source/sink directly at the rim.
  The result's `passed` follows all checks; failures are returned explicitly.
- Optional named-terminal transfer extraction delegates to the existing
  bounded-RHS method for equipotential terminals. Untied groups have multiple
  voltages and are explicitly unsupported by this initial transfer wrapper.
  Preparation/last-solve instrumentation is exposed. Solver sparse bytes exclude
  the composition wrapper's check matrix, original sheet matrices, geometry
  and maps; whole-model or peak-RSS memory is not measured.

Validation retains the 0.5% analytic gate and tighter existing case limits.
New strip/drop/current/loss/transfer comparisons use 1e-6 relative where
applicable. The unit-square unequal-load case has an independent four-edge
resistor reference: fractions 0.25/0.75 give nodal voltages -5/6 and -7/6 V
and loss 13/12 W at a 1 A sink. This would fail if the selection were tied
or silently assigned equal currents. Additional cases verify signed current
from two voltage sources, reversed resistor orientation, snapshot isolation,
one LU across load/reference changes and transfer extraction, invalid inputs,
and an externally driven rim. Deliberately omitting barrel heat makes power
fail while current/rim checks pass.

`experiments/phase1_network_cases.py` is a runnable example using only this
interface, not manual sparse wiring. Three 50 × 5 mm strips have copper
thicknesses 18/35/70 µm and rho = 1.724e-8 ohm metre. Two barrel segments
use 0.15/0.175 mm copper radii and 0.8/0.05 mm lengths. The source is the top
strip's outer pad at 3.3 V; the middle and bottom pads draw 0.1/0.2 A,
then 0.2/0.4 A. Expected segment currents are 0.3/0.2 A, then 0.6/0.4 A.
References use independent strip and annular-area barrel formulas.

| Strip cells per layer | Geometric / electrical nodes | Middle drop at first load (mV) | Bottom drop at first load (mV) | Largest relative drop error across both loads |
| --- | --- | --- | --- | --- |
| 8 × 3 | 108 / 90 | 3.528001868 | 3.534755914 | 2.8464e-13 |
| 16 × 6 | 357 / 321 | 3.528001868 | 3.534755914 | 3.4714e-13 |

Across these four direct cases: maximum relative branch-current error
3.3396e-11; maximum global current residual 4.4009e-13 A; independent power
residual 1.2065e-15 W; per-layer rim residual 8.0096e-12 A. All checks pass.
The hand-made strip fields are exact for linear FEM up to roundoff; this
near-zero error is not evidence for general geometric or physical accuracy.
Only numerical/physical checks gate the example; no timing threshold is claimed.

The composed annular-via test uses two separately meshed annuli, outer radii
1.5/1.0 mm, 35/18 µm sheets, the same copper radii, a 1.6 mm barrel and a
0.25 A sink. Against ring + barrel + ring, measured resistance errors are
0.0226758% at h = 0.1 mm (1,438 geometric nodes) and 0.00329075% at h =
0.05 mm (5,333 nodes). Both meet 0.5% and error decreases on refinement.
Current, power and both layer-rim checks pass. Each level's model/factors are
released before building the next; only scalar summaries survive. Gmsh is
still invoked externally. No existing experiment script was migrated.

Verification: final full suite **311 passed, no skips, 50.81 s**; focused
composition tests **32 passed, 2.22 s**. The runnable example exits successfully
with all four cases passing. `git diff --check` passes. Commands:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q -s tests/test_network.py
.venv/bin/python experiments/phase1_network_cases.py
```

Not run: production import or new private-board scenarios, new padne/KiPI or
full-3D comparisons, measurements, large-board/peak-memory profiling or remote
CI. No thermal or viewer changes. Terminal selections currently belong to
one sheet and may not overlap; geometry-based finite contacts, overlap/alias
resolution and importer integration remain later work. Changes are uncommitted;
no files were staged, pushed or merged by this session.


## 2026-11-15 — Phase 1 increment 4: finite-area contact selection

**Result / Recommendation: explicit finite electrodes and uniform-current area
loads pass analytic/convergence checks with unchanged electrical gates.
Recommendation: review these bounded contact models and remaining phase-1
coverage before proposing phase-1 closeout. No production contact/importer or
3D interface validation is claimed.**

Starting state verified: clean `phase1-solver-core`, HEAD `c9a3f2c`; Erekle
committed named network composition. Baseline rerun: **311 passed, no skips,
50.70 s**. No additional repository instruction file was found. Shapely 2.2.0
was already installed and declared; no dependency was added.

`pdnlab_dc.contacts.select_area_contact` and `Network.add_contact` accept an
explicit positive-area 2D polygon at mesh coordinates in metres. The entire
region must lie on nonoverlapping copper elements. Missing copper, contact
holes lying in mesh voids and overlapping mesh elements within a contact are
checked geometrically; the area is not silently clipped to the surviving
copper. Coverage uses a 1e-10 relative geometric tolerance, separate from and
without relaxing analytic/current/power gates.

The two explicit models are different physical idealizations:

- `equipotential`: an ideal electrode shorts only its specified area. Its
  boundary must conform to existing mesh edges (a union of full triangles).
  Materially cut elements raise with a request for a conforming mesh, instead
  of using nearest vertices or enlarging the electrode. It supports sources,
  sinks, branch endpoints and the existing optional transfer interface.
- `uniform_current`: the load has uniform prescribed current per area and
  leaves copper untied. Each triangle is clipped against the region. For its
  linear shape functions, integral(N_i) = clipped_area × N_i(clipped_centroid),
  which produces conservative nodal load fractions even for nonconforming
  patches, holes and multi-piece intersections. Support vertices may lie
  outside the polygon; this is FEM load integration, not contact enlargement.
  The same weights evaluate the area-average copper voltage. It is a sink
  model, not an equipotential voltage source or a single-node transfer port.

Prepared models expose scalar geometry/model summaries via `contact_info`;
`NetworkResult.contact_voltages_v` reports electrode voltage or the area mean.
The original selected-node voltage output remains available. Contact-selection
time is recorded separately from sheet assembly, composition and LU. No
whole-model memory or bottleneck claim is made. Existing snapshot and LU reuse
behaviour is retained. No existing experiment script or core check was removed.

Validation includes a positive-area patch entirely inside one triangle with
no mesh vertices inside it: its exact load fractions are 0.5/0.25/0.25, with
either triangle orientation. An asymmetric polygon hole verifies the exact
mean of a linear field. Positioned full-width electrodes give the analytic
strip resistance across their actual gap, including translated coordinates;
their fields/loss/transfer tests keep a 1e-6 relative limit. Invalid regions,
bad meshes, unresolved electrode edges and overlapping coarse support are
rejected. Failed selection leaves the builder unchanged.

Measured example: `experiments/phase1_contact_cases.py`, rho = 1.724e-8 ohm
metre, 35 µm copper, 1 A numerical sink per case. Each model/factorization is
released before the next level, with only scalar summaries/checks retained.
No external mesher or private board is needed for these examples.

**Finite circular electrode.** A fixed 128-sided contact polygon of radius
1.25 mm is placed at the centre of a 5 mm-radius polygonal disk. The outer
boundary is at 0 V. Copper under the electrode is tied; outside it the
reference is rho × ln(5/1.25)/(2πt) = 0.1086787928 mΩ. The polygon is unchanged
across radial refinements; circle-reference error includes polygon geometry.

| Radial cells | Mesh nodes | Electrode resistance (mΩ) | Relative circle-reference error | Mathematical point diagnostic (mΩ) |
| --- | --- | --- | --- | --- |
| 16 | 2,049 | 0.1084677681 | 0.1941728% | 0.3712254947 |
| 32 | 4,097 | 0.1086092683 | 0.0639724% | 0.4255444197 |
| 64 | 8,193 | 0.1086450184 | 0.0310773% | 0.4798705130 |

All finite-electrode levels meet 0.5% and error decreases. The point column
instead draws 1 A at the centre vertex alone, with no area model. Its voltage
keeps growing; each halving's increment matches rho × ln(2)/(2πt) within the
test's 1% diagnostic limit. Those values are not physical contact-resistance
predictions. Both types pass independent numerical current/power checks.

**Uniform-current patch.** A 50 × 5 mm strip has a source electrode covering
x = 0 to 5 mm at 3.3 V and a full-width uniform sink patch x = 20.3 to
30.7 mm. Its edges cut the load mesh triangles. Let a = 20.3 mm, d = 10.4 mm
and x_s = 5 mm. Lateral current is constant before a and falls linearly to
zero across d. Integrating its square gives loss/I² and mean drop/I equal to
(a-x_s+d/3)/(gW) = 1.8487847619 mΩ, where g = t/rho.

| Cells | Mesh nodes | Area-mean drop / I (mΩ) | Relative error |
| --- | --- | --- | --- |
| 40 × 8 | 369 | 1.8476518402 | 0.0612793% |
| 80 × 16 | 1,377 | 1.8485015270 | 0.0153201% |
| 160 × 32 | 5,313 | 1.8487132568 | 0.00386768% |

Both mean-voltage and independently integrated heat errors meet 0.5% at all
levels and decrease with refinement. Across all nine example cases, maximum
global current residual is 3.7681e-12 A and power residual is 3.0572e-15 W.
No vias are present in these contact examples; existing via regressions remain.
The README's finite-contact example was also run successfully.

Verification: final full suite **336 passed, no skips, 56.12 s**. Focused
contact/composition tests: **57 passed, 3.97 s** (25 new contact/mesh tests).
The example exits successfully with all nine cases, finite-error convergence
and the point-growth diagnostic passing. `git diff --check` passes. Commands:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q -s tests/test_contacts.py tests/test_network.py
.venv/bin/python experiments/phase1_contact_cases.py
```

Limits: no automatic contact-boundary remeshing, pad/importer integration,
contact resistivity, pressure-dependent distribution or 3D interface model.
The copper solver still requires disjoint terminal support. Disjoint physical
contacts sharing coarse basis vertices must be refined; overlapping selections
are not silently merged. Accuracy is established only for these synthetic
idealizations, not arbitrary board contacts. Not run: new private-board or
padne/KiPI cases, remote CI, hardware/full-3D correlation or large-board peak
memory/contact-scaling profiles. No thermal or viewer work. Changes remain
uncommitted; no files were staged, pushed or merged by this session.

## 2026-11-15 — Phase 1 scoped closeout review

**Result / Recommendation: verified clean `phase1-solver-core` at `e945357`;
the contact commit contains the expected eight files. No additional coding
blocker was identified in this scoped review. Recommendation: review the PR,
obtain passing analytic CI and let Erekle decide phase-1 acceptance/merge.**

`docs/phase1-review.md` maps the approved synthetic solver requirements to
implementation/tests, records remaining CI/maintainer acceptance steps and
separates production/hardware/performance limitations from phase-1 evidence.
The plan links to that review without declaring the phase closed.

The latest measured full suite remains **336 passed, no skips, 56.12 s**;
it was not rerun for this documentation/code-inspection review. No new
numerical results are claimed. `git diff --check` was run and passes.
Only the protective `.gitignore` is tracked beneath `tests/test boards/`;
no private-board content was added or read for the review.

GitHub's check-runs lookup for full SHA
`e9453574361cac7716a1d1ca766f9154eaef6401` returned “No commit found”. The
reviewed commit therefore has no verified remote CI result here. The workflow
runs on PRs and pushes to `main`, not standalone feature-branch pushes; push
the branch and open a PR to exercise the Python 3.11/Gmsh analytic gate.
KiCad's optional native integration may skip on CI, which does not install
KiCad; a matching full local test count is not the acceptance requirement.

No source/test code, dependency, private board or physics model was changed.
No new numerical solve, performance profile, remote CI run, reference-tool
comparison, hardware or 3D validation was performed. This is a scoped review,
not an independent physical-model audit. Review documentation is uncommitted;
no files were staged, pushed or merged by this session.

## 2026-11-15 — Maintainer acceptance of Phase 1 and verified merged-main CI

**Result / Recommendation: Erekle explicitly requested recording Phase 1
acceptance and the passing CI result. Phase 1's documented synthetic solver
scope is accepted. Recommendation: carry the existing limitations into Phase 2
planning; no production-import, hardware or full-3D validation is implied.**

Verified local starting state: clean `main` at
`a8dadf556bbabb91fa99e039cf5310ab5ef4519b`. GitHub reports that Erekle merged
[PR #8](https://github.com/erelezhava/PDNLab/pull/8) at that commit. No merge
was performed by this assistant. This entry supersedes the earlier scoped
review's pending-CI/maintainer-acceptance status, preserving its history.

Verified GitHub Actions evidence:

- [Workflow run 43740365125](https://github.com/erelezhava/PDNLab/actions/runs/43740365125),
  event `push`, branch `main`, head SHA matching the merge commit above:
  `status=completed`, `conclusion=success`.
- Job `pytest`: success; steps installing Gmsh, installing dependencies and
  `Run pytest -v`: success. The workflow uses Python 3.11.
- Detailed remote test counts were not retrieved. The last local numerical
  result remains **336 passed, no skips, 56.12 s**; it is not a remote CI count.
  KiCad is not installed by the workflow, so optional native integration may
  skip there. No test or numerical solve was rerun for this documentation-only
  update.

README, project plan and the Phase 1 review now record acceptance and link to
the merged PR/passing workflow. Accepted scope: prepared direct DC solves,
named synthetic network composition, reference-stable independent checks and
validated finite-area contact idealizations. Existing limits remain: no
production importer/contact remeshing, physical interface/3D correlation,
large-board peak-memory characterization, iterative backend, thermal or viewer.
Phase 2 implementation is not started by this update.

Verification: documentation consistency and `git diff --check` pass. No source,
tests, dependencies or private board data changed. Documentation remains
uncommitted in this checkout; no files were staged, pushed or merged.

## 2026-11-15 — Phase 2 small task: importer boundary and model contract

**Result / Recommendation: inspected the experimental reader's output and
recorded a minimum neutral-model proposal in `phase2-board-model-contract.md`.
Recommendation: implement only those records/validators as the next bounded
increment, before migrating XML parsing. Erekle retains schema decisions.**

Starting state: clean `docs/phase1-acceptance`, HEAD `050e702`. The current
checkout contains the acceptance documentation; no branch ref was changed.
Inspection covered `read_net_ipc2581` and its native-reader comparison shape.
The contract explicitly separates SI conversion, coordinate transforms,
per-layer copper, hole spans and electrical contacts, and unknown metadata
from setup assumptions. It preserves the production/synthetic boundary.

Verification: reader-field mapping and documentation consistency were checked;
`git diff --check` passes. No code, dependency, private board or experiment was
modified; no tests, numerical solves or new exports were run. The latest
local suite remains the previously measured 336 passed, no skips. No production
schema or KiCad 11 compatibility is claimed implemented. Work is uncommitted;
no files were staged, pushed or merged.

## 2026-11-15 — Phase 2 small task: immutable neutral records and validation

**Result / Recommendation: neutral records and aggregate validation are
implemented in `pdnlab_dc.board`. Focused tests: 44 passed in 0.39 s; full
regression suite: 380 passed, no skips, 54.38 s. Recommendation: review this
bounded model before the next XML adapter increment; production import is not
implemented and the development schema is not declared frozen.**

Starting checkout remained `docs/phase1-acceptance` at `050e702`, with the
preceding contract/log edits already uncommitted. Those edits were preserved;
no branch ref was changed because Git metadata remains read-only here.

Implemented frozen source, copper-layer, net, copper-feature, physical-pad,
circular-drill and aggregate board records. Constructors snapshot sequences,
require finite SI dimensions and valid 2D polygon topology, and check unique
IDs, net/layer/feature/drill references, increasing nonoverlapping layers and
ordered drill spans. Source units/affine-transform provenance are recorded,
not applied. Missing optional metadata and unassigned nets remain explicit.
Copper outlines and drills stay separate; no setup-dependent hole correction
or contacted-layer inference occurs.

Separate physical pad IDs may share a structured component/pin identity,
provided their net declarations agree. Different layer padstack shapes,
polygon holes, disconnected polygon parts and explicit unknown plating are
preserved. Tests cover these cases, immutable input snapshots, invalid geometry,
nonfinite/nonpositive dimensions, malformed provenance, duplicate IDs, dangling
or inconsistent references, and out-of-span copper. No automatic geometry
repair, union, XML parsing, mesh generation or solver adapter was added.

Validation commands:

```sh
.venv/bin/python -m pytest -q tests/test_board.py
.venv/bin/python -m pytest -q
```

`git diff --check` passes. Existing analytic regressions reran with the full
suite; no new real-board solve, export, standalone benchmark, remote CI or
KiCad 11 compatibility run was performed. No dependency or private board data
changed. README and the contract describe the development interface. Changes
remain uncommitted; no files were staged, pushed or merged.

## 2026-11-16 — Phase 2 small task: IPC-2581 header and stackup metadata reader

**Result / Recommendation: `pdnlab_dc.ipc2581_metadata` reads source provenance
and ordered copper layers from IPC-2581 revision C files in millimetres with
one explicit stackup. Measured in the assistant's cloud workspace, not on
Erekle's machine: focused tests 61 passed in 0.16 s; full suite 437 passed,
4 skipped, 19.59 s (the same commit before this change: 376 passed, 4 skipped).
Only hand-written XML was parsed; no real export, numerical solve or CI run.
Recommendation: rerun the suite locally and read one real KiCad 10 export
before starting the geometry increment. The Z origin, the recorded Y reversal
and the layer-function policy below are recommendations for Erekle to approve
or change, not project decisions.**

Verified starting state. The local checkout is on `phase2-board-model` at
`9a3c0c5442903d98ce103673662958cfb4781a86` ("Add immutable neutral board
records and validation"), and its local remote-tracking ref has the same hash.
65 of the 66 tracked files were compared byte for byte with that commit and
are identical; `tests/test boards/.gitignore` was not read. On GitHub the
commit is the head of PR #10, which is merged into `main` at
`7a38317119efa1b2bc3c72eef197d23bcc7ecb95`; the remote `phase2-board-model`
branch no longer exists, and local `main` is still at `a8dadf5`. So the
neutral-model increment is pushed and merged, and this work should start from
current `origin/main`, whose tree equals `9a3c0c5`. This assistant merged
nothing. Phase 2 CI status was not looked up.

Implemented `parse_ipc2581_metadata(bytes)` and `read_ipc2581_metadata(path)`,
returning a frozen `Ipc2581Metadata(source, copper_layers)`. It is deliberately
not a `BoardModel`: it has no nets, features, pads or drills, and the first
`SourceInfo.assumptions` entry says they were not read. The module uses the
standard library plus `pdnlab_dc.board`; it does not import the experiments.

Checks, each raising `ValueError` with the layer or element named:

- Root `IPC-2581` in namespace `http://webstds.ipc.org/2581`, `revision="C"`;
  exactly one `Ecad`, `CadHeader` and `CadData`; `CadHeader` units
  `MILLIMETER`. A `Content/Dictionary*` entry declaring other units is also
  rejected, so the recorded `mm` holds for the dictionaries the geometry
  reader will use. Zip-compressed input and malformed XML are rejected.
- `CadData/Layer`: nonblank unique `name`, a `layerFunction`. Copper is
  `CONDUCTOR`. `SIGNAL`, `PLANE`, `MIXED`, `CONDFOIL` and `CONDFILM` raise, so
  copper under another function is never silently dropped.
- Exactly one `Stackup` with exactly one `StackupGroup`. Every `StackupLayer`
  references a defined layer once and has a unique nonnegative integer
  `sequence`; the `sequence` attribute orders the stack, not document order.
  Every `CONDUCTOR` layer must be in the stackup.
- From the first to the last copper row, only `CONDUCTOR` and `DIEL*` rows are
  allowed, each with a finite positive decimal thickness, and two copper rows
  need a dielectric between them. Rows outside that span (mask, paste, legend,
  coating) are ignored and their thickness is not validated.

Conventions recorded in `SourceInfo.assumptions`: Z is zero at the outer face
of the first copper layer in stackup sequence and increases with sequence;
`z_m` is the copper centre, as in the exp08 readers. Thicknesses are summed
exactly as decimals in millimetres and rounded once to metres. Layer `id` and
`label` are both the exported layer name. `xy_transform` is
`(1e-3, 0, 0, 0, -1e-3, 0)`: millimetres to metres with IPC Y reversed once,
the frame the contract recommends. It is recorded only; no geometry exists
here to apply it to. The input hash is SHA-256 of the bytes that were parsed.
`exporter_version` is `"<name> <revision>"` from
`HistoryRecord/FileRevision/SoftwarePackage` when the file declares exactly one
package with both attributes, otherwise `None`; it is the file's own claim.

Tests in `tests/test_ipc2581_metadata.py` use an asymmetric four-copper stack
(35/17.5/70/18 um copper, 0.1/1.24/0.2 mm dielectrics, masks outside) with
hand-computed centres 17.5, 143.75, 1427.5 and 1671.5 um, and the different
centres obtained when the sequence is reversed. They also cover shuffled
document order, ignored outer rows including zero-thickness coating rows, a
single copper layer, exponent/padded decimals, hash/exporter provenance, 48
parametrized rejections for wrong namespace/revision/units and missing,
duplicate or invalid header, layer and stackup data, and truncated, empty,
compressed and non-byte input. Nine deliberate one-line bugs (Z
origin, half thickness, ordering, Y sign, unit scale, hash input, exporter
rule, revision check, dielectric accumulation) each made at least one test
fail.

Validation commands, run on a clone of `9a3c0c5` plus these files (Python
3.13.18, numpy 2.5.5, scipy 1.18.1, shapely 2.2.0, pytest 9.2.0, Gmsh 4.12.1):

```sh
python -m pytest -q tests/test_ipc2581_metadata.py   # 61 passed
python -m pytest -q                                  # 437 passed, 4 skipped
```

The full suite also gave 437 passed, 4 skipped under Python 3.11.18 and 3.14.6.
The four skips need KiCad CLI, the StickHub project or local private boards,
none of which exist in that workspace. `git diff --check` passes. Not run:
anything in Erekle's `.venv`, any real or private board, any export, any
solve, remote CI, KiCad 11.

Limitations and open points:

- No real export has been read by this module. The fixtures follow the layout
  of the existing audit fixtures. Element names for the exporter version were
  taken from the KiCad development-branch exporter source, which is not
  export evidence for KiCad 10 and not compatibility validation.
- Dielectrics split into sublayers are untested; check their exported
  thickness and sequence values on a real file before relying on Z.
- Rigid-flex or multi-group stackups, inch/micron files and revision B are
  rejected, not converted. `overallThickness`, layer `side`, `Step`, profile
  and datum are not read. Which copper is the physical top is not asserted.
- Parsing uses `xml.etree.ElementTree` without extra entity hardening; inputs
  are assumed to be the user's own exports.

Stopped here as instructed: no tracks, pads, zones, drills, nets, cleanup,
meshing, setup files, CLI or real-board solve. No dependency or private board
data changed. Changes are uncommitted; nothing was staged, pushed or merged.

phase2-ipc2581-zone-fills
## 2026-11-16 — Metadata reader on one real KiCad 10 export; conventions approved

**Result / Recommendation: Erekle ran `read_ipc2581_metadata` on the
experiment-6 export of one private four-layer board. It returned exporter
version `KiCad 10.0.8` and four copper layers in top-first order without
raising, and Erekle confirmed that the copper thicknesses and the dielectric
gaps derived from the printed centres match Board Setup. His local full suite
gave 441 passed in 32.98 s. Erekle approved three conventions: Z = 0 at the
outer face of the first copper layer, IPC Y reversed once in the stored XY
frame, and `CONDUCTOR` as the only copper layer function for now.
Recommendation: the next increment is nets plus filled-zone contours only.**

Both runs were made by Erekle in his `.venv` (Python 3.14); the assistant saw
only the pasted output and did not open the board or its export. The board's
values are not recorded here because the board is private. The pasted suite
summary shows no skips, against 437 passed and 4 skipped in the assistant's
workspace, where KiCad CLI, StickHub and the private boards are absent.

Limits of this evidence: one board and one exporter version. Its stack is
symmetric, so it cannot reveal a mirrored Z computation; that case is covered
only by the synthetic asymmetric fixture. It has no dielectric sublayers.
Other copper layer functions (`SIGNAL`, `PLANE`, `MIXED`, `CONDFOIL`,
`CONDFILM`) still raise; supporting them is deferred to other CAD exporters.

GitHub shows PR #11 merged into `main` at
`33439adb557a70efe250e1f6584c900c4d3c61b4`, with the same tree as the reader
commit `843aa5b`. This assistant merged nothing and did not verify the CI
result for that commit. No code or test changed in this documentation-only
entry.
main

## 2026-11-16 — Phase 2 small task: IPC-2581 nets and filled-zone contours

**Result / Recommendation: `pdnlab_dc.ipc2581_fills` returns `Net` records and
one `CopperFeature` of kind `fill` per zone contour, in metres with holes. It
counts tracks, pads and vias for the next increments, and raises on any other
copper. Measured in the assistant's cloud workspace, not on Erekle's machine:
focused tests 59 passed in 0.28 s; full suite 496 passed, 4 skipped, 24 to 30 s
on Python 3.11.18, 3.13.18 and 3.14.6 (the same commit before this change: 437
passed, 4 skipped). Only hand-written XML was parsed. Erekle decided the
treatment of copper other than fills, tracks and pads (below) and approved the
slit handling. Recommendation: run the reader
on the real experiment-6 export before the track increment.**

Verified starting state. `origin/main` is `33439ad` (merge of PR #11). Its
tests workflow on `main` (run 43796870347, job `pytest`) concluded `success`
for that commit. Erekle's checkout is on `docs/metadata-real-export-check`
(read from his `.git/HEAD`); his `docs/DECISIONS.md` is byte-identical to that
branch's pushed head `ff1ae29`, so the real-export entry is committed and
pushed. It is in open PR #12, whose CI run 43802617912 concluded `success`;
PR #12 is not merged. This work branches from `origin/main`, so it does not
contain that entry. This assistant merged nothing.

Open questions settled by reading KiCad's exporter source,
`pcbnew/pcb_io/ipc2581/pcb_io_ipc2581.cpp` at tag `10.0.8` (commit
`e432beac2668303194ba2040274f9e5efe773fc0`), and the zone filler at the same
tag. This is source reading, not export evidence:

- Nets are written only as `Set@net`. `generateLogicalNets` is defined but
  never called and there is no `PhyNet`/`PhyNetGroup` output. `net` is left
  out for net code 0, which is how "no net" is marked. Names go through
  `genString`: in revision C `:` becomes `_`, and `_N` is appended if the name
  clashes with another generated name, so exported net names can differ from
  KiCad's.
- Copper `Contour`s are not all zone fills. Board zones are `Contour`s in the
  net's `Set/Features/UserSpecial`. Teardrops, copper text (knockout glyphs)
  and footprint-owned zones get their own `Set`, marked by
  `geometryUsage="TEARDROP"`, `"TEXT"` or a `componentRef` with no `net`.
  Board graphic polygons, rectangles and circles are `UserPrimitiveRef`s into
  the user dictionary.
- One net `Set` per layer mixes fills, track `Line`/`Arc` and via `Pad`s
  (`padUsage="VIA"`); pin pads go in a second `Set`. Board graphic segments
  and arcs land in the same `UserSpecial` as tracks and cannot be told apart.
- The zone filler calls `Fracture()`, so each exported fill is one ring that
  reaches every hole along a zero-width slit: an edge followed later by the
  same edge reversed. Shapely 2.2.0 reports such a ring invalid ("Ring
  Self-intersection"), so a strict no-repair reader would reject every real
  fill with a hole. Consistent with this, experiment 6 matched all 11 saved
  fill polygons of the real board against exported vertex cycles without
  using `Cutout`s.

Implemented `parse_ipc2581_fills(bytes)` / `read_ipc2581_fills(path)`. The
metadata module gained `parse_ipc2581_tree`, returning the parsed root with the
metadata, so header, units, layers and provenance are parsed once and checked
only there; its public behaviour and tests are unchanged. The result,
`Ipc2581ZoneFills`, holds `source`, `copper_layers`, `nets`, `fills`, `unread`,
`uninspected_layers` and a `scope` text saying tracks, pads, vias and drills
were not read. It is not a `BoardModel`;
one is built internally only to reuse its ID and reference checks.

- Fills: `Polygon` rings and `Cutout`s of `PolyBegin` and `PolyStepSegment`
  only, closed, exact decimals; no `Xform` and no nonzero `Location` on any
  containing element. The recorded transform is applied once and each value
  rounded once to metres. One feature per `Contour`; ID `fill:<layer>:<k>`,
  `source_ref` the element path. Nothing is unioned, simplified or repaired;
  an invalid polygon raises with layer, net and path.
- Counted in `unread` by kind, layer and net, without reading or validating
  their geometry, because the next increments read them: track segments and
  arcs (copper graphic segments and arcs are indistinguishable from tracks and
  will be read with them), pads and via pads. Non-copper `LayerFeature`s are
  listed in `uninspected_layers`.
- Raise: teardrops, copper text, footprint graphics and `UserPrimitiveRef`
  board graphic shapes on copper, with or without a net; footprint-owned zone
  fills; curved steps, transforms, offsets, unknown elements or namespaces,
  unknown `Set` attributes (such as `polarity`), `geometryUsage` or
  `padUsage` values, net lists, blank net names, duplicate or undefined
  `LayerFeature` layers, other than one `Step`.

Decisions by Erekle (2026-11-16), after the first draft of this reader counted
teardrops, copper text and graphics instead:

- Any copper on a net that is to be simulated must be included. So no copper
  may be counted and left out unless a planned increment reads it; copper
  text and graphics are not on his copper layers, and they now raise. The
  assistant applied the same rule to teardrops, which carry a net: they raise
  until a reader for them is agreed. Erekle checked the experiment-6 export:
  it contains no `geometryUsage="TEARDROP"` set, so no teardrop fixture is
  needed now.
- A footprint-owned zone fill raises, because the export drops its net; the
  workaround is to move the zone to board level.

- Approved: slit handling. Each edge
whose exact reverse is also an edge is removed, splitting the ring into one
outline and its holes (the criterion of KiCad's `unfractureSingle`). Pairs must
nest, exactly one ring may keep the outline's orientation, and the result must
be a valid polygon; otherwise it raises. No vertex moves and the area is
unchanged. Without this, real fills with holes cannot be read at all under the
no-repair rule.

Tests in `tests/test_ipc2581_fills.py` use an asymmetric five-vertex outline at
an asymmetric position with every vertex checked in metres, an explicit
cutout, KiCad's slit form with one hole, with two chained holes, and starting
inside a hole, two nets on two layers plus unassigned copper and a net seen only
on a drill layer, stable IDs and paths, the unread counts and scope, 40
parametrized rejections and 5 for teardrop, text and graphic copper. Ten
deliberate one-line bugs each made at least one test fail: Y not reversed (4
failed), Y reversed twice (4), millimetres kept (4), explicit cutout dropped
(3), slit holes dropped (2), slits not split (13), nonzero `Location` ignored
(3), a net invented for unassigned copper (11), teardrop/text/footprint-graphic
sets counted instead of raising (3), board graphic shapes counted (2).

Validation commands, run on a clone of `33439ad` plus these files (numpy 2.5.4,
scipy 1.18.1, shapely 2.2.0, pytest 9.2.0, Gmsh 4.12.1):

```sh
python -m pytest -q tests/test_ipc2581_fills.py   # 59 passed
python -m pytest -q                               # 496 passed, 4 skipped
```

The four skips need KiCad CLI, the StickHub project or local private boards.
`git diff --check` passes. One 200,000-vertex synthetic outline took 1.84 s,
so a large board with millions of fill vertices may take tens of seconds. Not
run: anything in Erekle's `.venv`, any real or private export, any solve,
remote CI for this branch, KiCad 11.

Limitations: the exporter findings come from one KiCad version's source, not
from files; fills are taken as saved, not checked for freshness; feature IDs
depend on document order, so they are stable for one file, not across
re-exports; overlapping fills of the same net stay overlapping; content that
is only counted is not validated; parsing uses `xml.etree.ElementTree` without
extra entity hardening.

Stopped here as instructed: no tracks, pads, drills, cleanup, meshing, setup
files, CLI or real-board solve. No dependency or private board data changed.

## 2026-11-16 — Fills reader on one real KiCad 10 export; increment closed

**Result / Recommendation: `read_ipc2581_fills` read the experiment-6 export of
Erekle's private test board without raising: 141 nets, 11 fills, 183 holes. The
fill count matches experiment 6, which matched 11 saved fill polygons. Every
hole came from splitting KiCad's slits, so this is the first real-file evidence
for the approved slit handling. The nets and filled-zone increment is closed.
Recommendation: start the tracks increment; arc direction still needs a real
export that has an arc on copper.**

Run by Erekle on his machine, `main` at `c19a870` (PR #13 merged), with the
project `.venv`; output pasted into the session. Counts and layer names are
logged with his permission; net names and stack values are not.

```sh
.venv/bin/python -c "from pdnlab_dc.ipc2581_fills import read_ipc2581_fills as r; x=r('tests/test boards/test brd 1/_exp06/board.xml'); print(len(x.nets),len(x.fills),sum(len(f.geometry.interiors) for f in x.fills)); print(sorted({(u.kind,u.layer_id) for u in x.unread})); print(x.uninspected_layers)"
```

- Output line 1: `141 11 183` (nets, fills, holes).
- Unread kinds by layer: pads on F.Cu, In1.Cu, In2.Cu and B.Cu; via pads on
  all four; track or graphic segments on F.Cu, In2.Cu and B.Cu. No arcs on
  copper, and no copper text, graphics, teardrops or footprint-owned zones
  (any of these would have raised).
- Uninspected layers: F.Mask, B.Mask, F.Silkscreen, B.Silkscreen, F.Paste,
  B.Paste, User.Eco2, Edge.Cuts, B.Courtyard, F.Courtyard, B.Fab, F.Fab and
  the drill layer F.Cu_B.Cu.

Erekle's earlier grep found 10 `<Arc ` elements in this export and no
`geometryUsage="TEARDROP"`. Since the reader counted no arc on any copper layer,
those arcs are on non-copper layers. So this board cannot check the arc
direction convention for the tracks increment; a small public KiCad 10 board
with one arc track is needed for that.

State of the merges: PR #12 merged at `85f2b6c`; PR #13 merged at `c19a870`.
CI passed for PR #13 (run 43809395145) and on `main` after the merge (run
43809508904). Resolving the PR #13 conflict put the PR #12 entry after the fills
entry; this change moves it back to date order without changing its text.

Not checked: fill geometry against KiCad beyond the count (experiment 6 already
matched vertex cycles); holes against the board file; anything in a solve.

## 2026-11-16 — Phase 2 small task: IPC-2581 tracks (segments and arcs)

**Result / Recommendation: tracks are read. Each IPC-2581 `Line` and `Arc` on
copper becomes a `CopperFeature` of kind `track`, outlined with round ends at a
1 µm chord error. On the public arc board all 13 tracks (7 segments, 6 arcs)
match KiCad's own `.kicad_pcb`, arc direction included. On Erekle's private test
board all 785 segments match (it has no copper arcs). Cloud workspace: 104
focused tests pass; full suite 541 passed, 4 skipped on Python 3.11.18, 3.13.18
and 3.14.6. Recommendation: merge, then start the pad increment.**

Worked step by step with Erekle, who asked for that pace. Decisions by Erekle
(2026-11-16):

- Round geometry: at most 1 µm chord error, the same rule for track caps and arc
  edges now and for round pads and via rings later.
- Every vertex on its true circle (choice A). Chords cut a little copper off
  convex pieces and add up to 1 µm on an arc's inner edge, which is concave; the
  total area is always slightly below exact. Rejected: pushing inner-edge
  vertices outward so no copper is ever added.
- One feature per segment or arc, no union.
- Arcs supported, not raised.
- An arc's start and end radii may differ by up to 1 µm (KiCad rounds the
  centre); edges use their mean radius and the caps sit exactly on the exported
  end points.
- One copper reader (option A): `ipc2581_fills` is renamed `ipc2581_copper`
  (`read_ipc2581_copper`, `Ipc2581Copper`, new field `tracks`), so the strict
  structure checks stay in one walk. Fill behaviour is unchanged.

Evidence, in the order it was gathered:

1. Arc format, first +3.3V arc of `tests/fixtures/arc_track`. The `.kicad_pcb`
   gives start, mid and end; the export gives start, end, centre and
   `clockwise="true"`, with Y negated. Measured from the exported centre: start
   −135.0°, KiCad's mid −157.5°, end 180°, so the flag is clockwise in IPC Y-up
   coordinates. Radii 5.3112912, 5.311298 and 5.3113 mm: the centre is written
   with 5 decimals, so start and end disagree by about 9 nm.
2. That arc as an outline: 77 vertices (outer edge 21, end cap 18, inner edge 20,
   start cap 18), valid, area 0.054% below exact. On three test arcs (that one,
   R 5 mm over 270°, R 0.5 mm with w 0.6 mm), edge midpoints lay up to 0.97 to
   0.99 µm inside and up to 0.96 to 0.99 µm outside the exact outline; areas
   −0.054%, −0.021%, −0.234%.
3. `tests/test_arc_track_fixture.py` parses the `.kicad_pcb` independently of the
   reader and checks all 13 tracks: same nets and kinds; each KiCad arc midpoint
   inside exactly one arc outline and the point opposite it across the circle
   outside; every arc vertex within 1 µm of the band edge or a cap; every segment
   vertex at exactly half the width from KiCad's centre line; areas below exact.
   A 13 µm segment KiCad left between an arc and a pad is effectively a dot whose
   area is ~0.5% below a true circle (an 18-step cap), so the area check allows
   1%. All widths on this board are 0.5 mm, so width handling is checked only by
   the synthetic tests.
4. Erekle's private board, run on his machine with a scratch script
   (`check_real_tracks.py`, not committed) applying the same checks, output
   pasted: per copper layer, KiCad segments/arcs against exported tracks: F.Cu
   480/0 and 480/0, In1.Cu 0/0 and 0/0, In2.Cu 1/0 and 1/0, B.Cu 304/0 and 304/0.
   Nets per layer and kind equal. 785 segments checked, 0 failed; 0 arcs. Fills
   11; unread kinds pad and via pad. He also reported the full suite passing on
   his machine with this branch applied; the count was not pasted.

Tests: `tests/test_track_outline.py` (23: step counts, inscribed vertices and
chord error, areas, zero-length segment, both arc directions, the fixture arc,
rejections), `tests/test_ipc2581_copper.py` (78: the 59 fill tests plus tracks
transformed exactly once, Y reversed once, direction flag, width, IDs and paths,
13 rejections), `tests/test_arc_track_fixture.py` (3). Deliberate one-line bugs
each made tests fail. In `track_outline`: chord tolerance doubled (13 failed),
direction flag ignored (2), caps turned the wrong way (4), full width used as
radius (4), caps coarsened (3), radius check removed (1), fold-over check
removed (2). In the reader: Y not reversed, no transform, millimetres kept, or
transformed twice (3 each), `clockwise` inverted (1), width halved (5),
tolerance not converted to source units (1), `lineEnd` unchecked (1),
`Location` check limited to contours (1). Against the arc board test alone:
`clockwise` inverted (1 of 3), Y not reversed (3 of 3), width halved (2 of 3).

Not run or not covered: arcs on a real multilayer board; real tracks of
different widths; track reading time on a large board; KiCad 11. Overlaps
between tracks, pads and fills are left for the cleanup increment. Copper
graphic segments and arcs are read as tracks; a dashed or square-ended one
raises.

Stopped after tracks: no pads, drills, union, meshing, setup, CLI or solve.

## 2026-11-21 — Phase 2 small task: IPC-2581 pads and via copper

**Result / Recommendation: pads and vias are read. Each IPC-2581 `Pad` on copper
becomes a `CopperFeature` of kind `pad` or `via`, a solid outline built from the
export's shape dictionary and turned by its rotation. Against KiCad's own SVG
plot of each copper layer, all 36 pad and via shapes of the public pad board and
all 1,206 of Erekle's private test board (394 pads, 812 vias) match within 1% of
their area; worst 0.433% and 0.645%, both the inscribed-circle deficit of a small
round shape. Per-layer counts equal KiCad's file on both boards. Cloud
workspace: 130 focused tests pass; full suite 593 passed, 4 skipped on Python
3.11.18, 3.13.18 and 3.14.6; Erekle's machine 597 passed. Recommendation: merge,
then start the drills increment.**

Worked step by step with Erekle. Decisions by Erekle (2026-11-21):

- Support all six KiCad pad shapes: circle (`Circle`), rectangle (`RectCenter`),
  oval (`Oval`), round-rect (`RectRound`), chamfered rect (`RectCham`), and
  custom or trapezoid (`Contour`). Any other shape raises.
- Rotation: the shape is turned counter-clockwise in IPC (Y-up) coordinates by
  the pad's `Xform rotation` about its `Location`. Any other `Xform` attribute
  raises.
- Round parts use the track rule: 1 µm chord error, vertices on the true circle.
- Through-hole pads and vias are read now, as solid outlines on every copper
  layer the export lists them on (kinds `pad` and `via`). Holes and barrels
  come with drills; until then the result says outlines are solid.
- IDs: `pad:<layer>:<component>.<pin>`, with `#2`... for a repeated pin, and
  `via:<layer>:<k>`; the element path stays in `source_ref`.
- Three KiCad exporter limits are documented, not detected (see below).
- Custom pads: accept from KiCad's source that their outline is written with Y
  flipped; revisit only if a real board uses custom pads.
- Thermal reliefs: check later with a public board, before copper cleanup
  (roadmap increment 7).
- Keep the pad board's SVG plot in the repository as a permanent test.

Evidence, in the order it was gathered:

1. One pad, `tests/fixtures/arc_track` R1 pad 1. KiCad: footprint at (144.31,
   80.05) rotated −90°, pad local (−1, 0), round-rect 1.2 × 1.4 mm, ratio
   0.208333. Export: `Location` (144.310, −79.050), already absolute with Y
   negated; `Xform rotation="270.0"`; `RectRound` 1.20 × 1.40, radius 0.250
   (= 0.208333 × 1.2), all corners `true`. A 1.2 × 1.4 rectangle looks the same
   turned 90° or 270°, so this board could not show the rotation direction.
2. KiCad 10.0.8 exporter source (`pcb_io_ipc2581.cpp`): one dictionary shape per
   KiCad pad shape as in the table of the contract doc; `Xform` carries only the
   pad's absolute rotation; shapes use `PADSTACK::ALL_LAYERS`; `Location` adds
   the pad offset without rotating it; `RectCham` is written without the rounding
   of a rounded chamfered pad.
3. `tests/fixtures/pad_shapes`, a public 4-layer board Erekle drew for this:
   round-rect, oval, circle, custom (triangle) and one-corner chamfered pads at
   15, 20, 30, 45 and 60°, a through-hole connector, flipped copies on the back,
   3 vias, a pad with no net. Export: no back-side pad has a mirror flag; KiCad
   stores flipped pads already mirrored (R4's chamfer is `bottom_left`, JP2's
   custom points have Y negated) and the export copies that. Through-hole pads
   and vias appear on all four copper layers.
4. That board against KiCad's SVG plot (`kicad-cli pcb export svg --layers
   F.Cu,In1.Cu,In2.Cu,B.Cu --mode-multi --exclude-drawing-sheet
   --drill-shape-opt 0 --black-and-white`), in board millimetres, measuring the
   area of the symmetric difference over the pad's area: vias 0.433% (an exact
   SVG circle against our 39-vertex one), round-rects 0.12 to 0.30%, chamfered
   R3/R4 pad 1 0.005% and 0.003%, custom triangles 0.006 to 0.009%. Each plot draws
   exactly as many pad and via shapes as were read. Controls with the reader
   broken on purpose: rotation clockwise 99.8% worst mismatch, oval sides
   swapped 18.3%, chamfer corners swapped 3.0%; a mirrored custom pad stayed at
   0.43% because the triangle is symmetric, so a synthetic L-shaped custom pad
   test was added, which fails on that mirror.
5. Erekle's private board, run on his machine with a scratch script
   (`check_real_pads.py`, not committed) on a fresh export and SVG plot of the
   same board version, output pasted. KiCad pads/vias against exported per layer:
   F.Cu 263/203 and 263/203, In1.Cu 13/203 and 13/203, In2.Cu 13/203 and 13/203,
   B.Cu 105/203 and 105/203. Shapes used: SMD circle 100, rect 74, round-rect 190;
   through-hole circle 10, rect 3; 2 non-plated holes without copper, neither
   written nor counted. No per-layer padstack, pad offset, rounded chamfer or
   custom pad. Plot comparison: 394 pads and 812 vias, none above 1%, worst
   0.645% for each, which is 1 − sin(11.25°)/(11.25° in radians), the deficit of
   a 32-vertex circle. Fills 11, tracks 785, nothing unread.

Tests: `tests/test_pad_outline.py` (27), `tests/test_ipc2581_copper.py` (100: the
fill and track tests, pads and vias on every layer, rotation direction with a
one-corner chamfer, every dictionary shape, custom-pad handedness, unassigned
pads, repeated pins, nested via sets, 15 pad rejections, 2 dictionary
rejections), `tests/test_pad_shapes_fixture.py` (3, against the committed SVG
plot, 1% limit). Deliberate one-line bugs each made tests fail. In `pad_outline`:
rotation clockwise (1 failed), rotation ignored (1), holes dropped by `place` (1),
corner names shuffled (4), radius used as diameter (1), oval on the short axis
(3), corner radius ignored (6), chamfer limit removed (1). In the reader: pad Y
not reversed (3), not transformed (3), rotation ignored (2) or negated (2),
chamfer corners ignored (1), via labelled `pad` (2), repeated pins not numbered
(1), oval sides swapped (1), `Location` y ignored (2), extra `Xform` attributes
allowed (1), custom pad mirrored (1, after the L test). Against the SVG test
alone: rotation negated (2 of 3), chamfer corners swapped (2), oval sides swapped
(1), pad Y not reversed (2), round-rect radius halved (1).

Corrections made during the work, recorded so the numbers can be trusted:
same-size one-line bugs written in the same second as the original can run from
Python's cached bytecode, so a check can test the wrong code. Found when a
restored module still failed; from now on bug checks run with
`PYTHONDONTWRITEBYTECODE=1`. The track increment's checks were re-run that way
and all still fail as recorded. Test expectations corrected, not the reader:
circle vertex count (61, not a hand estimate of 49), small-circle area (about
0.4% below exact), an oval's apex is not a vertex, the custom pad's area (1.875
mm²), B.Cu pad count (11), and a corner test that took its corner table from the
module under test.

Not run or not covered: an export with an asymmetric custom pad; per-layer
padstacks, offsets and rounded chamfers (not on either board; undetectable);
thermal reliefs where fills meet pads (planned); KiCad 11. The SVG files are
KiCad output and keep its trailing spaces.

Stopped after pads and via copper: no drill holes, barrels, union, meshing,
setup, CLI or solve.

## 2026-11-21 — Phase 2 small task: IPC-2581 drill holes

**Result / Recommendation: drill holes are read. Each IPC-2581 `Hole` becomes a
`DrillFeature` (kind via, pin or mechanical, span from its drill layer), linked
by `drill_id` to the pad and via copper it passes through; holes are recorded,
not cut. All 5 holes of the public pad board and all 218 of Erekle's private
test board match KiCad's board file in position, drill size, span, plating and
net. Cloud workspace: 127 focused tests pass; full suite 617 passed, 4 skipped
on Python 3.11.18, 3.13.18 and 3.14.6; Erekle's machine 621 passed.
Recommendation: merge, then the thermal-relief board.**

Worked step by step with Erekle. Decisions by Erekle (2026-11-21):

- One `DrillFeature` per `Hole`, ID `drill:<hole name>`; `VIA` → via, `PLATED` →
  pin, `NONPLATED` → mechanical; span from the drill layer's `Span`.
- A hole links to the pad and via copper of the same padstack and net at
  exactly its position, on each layer of its span. More than one candidate on a
  layer raises; a plated hole with no copper anywhere raises; a non-plated hole
  without copper, and a span layer without copper, are allowed.
- Holes are not cut from pad and via copper in the importer. Cutting uses the
  finished-hole size from the setup file, in the cleanup increment, as the
  Phase 2 contract requires. The exported diameter is KiCad's drill value.
- Plating thickness and barrels are not the importer's: setup values, modelled
  by the contact model increment with Phase 1's barrel model.
- Slots (`SlotCavity` in a `ROUT` layer), backdrill references and nonzero hole
  tolerances raise. Other spans are read generically.

Raised by Erekle, deferred to later increments: mesh sizes around holes, and the
radius at which holes are cut. Recorded starting points from experiment 8 (this
log, 2026-11-15): h = 0.5 mm, via holes as 96-sided polygons, element size
growing 0.15 mm per mm from holes and from through-hole pads; without local
refinement results were up to 1.9% low, from 0.5 mm thermal spokes covered by one
or two elements; between refined levels the largest drop changed by at most
0.37%. Experiment 8 cut holes at drill radius + 25 µm plating; whether that or
the drill radius is right depends on whether KiCad's drill value is the drilled
or the finished size, to be settled for the setup file, ideally from the fab.

Evidence, in the order it was gathered:

1. Existing board model (Phase 2 increment 1): `DrillFeature` with position,
   diameter, plating, start and end layer and kind; `CopperFeature.drill_id`; the
   contract rules that a span does not by itself mean contact and that
   finished-hole size and plating are setup assumptions.
2. One via and one through-hole pad of `tests/fixtures/pad_shapes`. KiCad: via
   at (137.29, 100.5), drill 0.3, layers F.Cu–B.Cu; J1 pad 1 drill 1.0. Export:
   drill layer `F.Cu_B.Cu` with `Span`; `Hole H1 diameter="0.30"
   platingStatus="VIA"` at (137.290, −100.50) in a `Set geometry="PADSTACK_9"`;
   `Hole H4 diameter="1.0" platingStatus="PLATED"` at J1 pad 1's exact
   `Location`, same padstack and net. No element names the pad of a hole.
3. KiCad 10.0.8 exporter source: drill layers per via span, pad holes always
   F.Cu–B.Cu, oblong holes to a `ROUT` slot layer as `SlotCavity`, backdrill
   references added to holes, tolerances written as 0.0.
4. Inventory of the private board (scratch script, counts pasted): 203 through
   vias (68 × 0.2, 91 × 0.3, 44 × 0.35 mm), 13 plated pin holes of 1.0 mm, 2
   non-plated holes of 2.6 mm; the export has the same 218 holes, all in
   `F.Cu_B.Cu`; no blind or buried vias, slots, backdrills or hole offsets; 216
   holes meet exactly one pad on each of the four layers, the 2 non-plated ones
   none.
5. Public pad board against its `.kicad_pcb` (`tests/test_pad_shapes_fixture.py`,
   2 new tests): each of the 3 vias matches a KiCad via within 1 nm, drill 0.3
   mm, span, net, with via copper linked on all four layers; each of the 2 pin
   holes matches its pad's drill, plating and net and lies at its pads' centre
   on all four layers.
6. Private board, same checks (scratch script `check_real_drills.py`, not
   committed; output pasted): 203 KiCad vias against 203 via drills, no
   problems; 13 plated pin holes against 13 pin drills, no problems; 2
   non-plated holes as 2 mechanical drills; 216 pads and vias linked on each of
   the four copper layers.

Tests: `tests/test_ipc2581_copper.py` now 122 (22 new: positions, diameters and
spans; via and pin linking; non-plated holes with and without copper; a blind via
on a three-copper-layer stack that must not claim copper on B.Cu; 14 hole and
link rejections; 3 span rejections; the slot layer). The synthetic test board's
drill layer, written before drills were read, was made realistic (tolerances,
a via hole under its via pads, a separate non-plated hole). Deliberate one-line
bugs, run with `PYTHONDONTWRITEBYTECODE=1`, each made tests fail: hole Y not
reversed (1), diameter left in mm (1), vias read as pins (16), span start and end
swapped (17), span ignored (1, the blind-via test), net left out of matching
(1), ambiguity unchecked (1), plated holes without copper allowed (3), tolerances
ignored (1), drill layers skipped (22). Against the pad-board test alone: hole Y
not reversed, diameter in mm, pads never linked, vias read as pins (2 each).

Not run or not covered: real exports with blind or buried vias, slots or
backdrills (none on either board); hole cutting, barrels and plating (later
increments); KiCad 11.

Stopped after reading and linking holes: no cutting, barrels, union, meshing,
setup, CLI or solve.

## 2026-11-21 — Phase 2 small task: thermal reliefs (zone fills meeting pads)

**Result / Recommendation: thermal reliefs need no reader change. KiCad writes
the spokes as part of the zone's fill outline, and the copper reader already
returns them: on Erekle's public thermal board every pad and via meets the fill
as KiCad filled it, and the fills match KiCad's SVG plot. Cloud workspace: 3 new
tests pass; full suite 620 passed, 4 skipped on Python 3.11.18, 3.13.18 and
3.14.6; checked on current `origin/main` before delivery. Merged as PR #21.
Recommendation: close the importer readers (increments 1 to 7) and start
assembling a full `BoardModel`.**

Worked step by step with Erekle. Board drawn by Erekle (2026-11-21), public,
in `tests/fixtures/thermal_relief`: 4 copper layers; a GND zone on F.Cu
(thermal gap 0.5 mm, spoke width 0.3 mm) and one on In1.Cu and In2.Cu (gap
0.5 mm, spokes 0.5 mm, priority 1); a through-hole test point TP1 (2 mm round
pad, 1 mm drill); an 0603 resistor R1 at 30° with pad 2 on GND and pad 1 on
+3V3; an SMD test point TP2 set to a solid zone connection; two GND vias. B.Cu
has no zone. Exported with KiCad 10.0.8 as for the pad board (IPC-2581 and
per-layer SVG plots); the KiCad project itself is kept outside the repository.

Method: for each pad, the ring from its edge out to half the thermal gap is
intersected with the fill. No fill in the ring means isolated, one piece
filling over 95% of it means solid, otherwise each piece is a spoke and its width
is its area divided by the ring width. Spoke widths are compared with the zone
settings read from the `.kicad_pcb`, not typed into the test. KiCad's plotted
fills are made valid with Shapely on the reference side only; the reader never
repairs geometry.

Measured (scratch scripts first, then the committed test):

| Pad | Layer | Connection | Spoke width | Overlap with pad |
| --- | --- | --- | --- | --- |
| TP1 | F.Cu | 4 spokes | 0.301 mm | 0.6023 mm² |
| TP1 | In1.Cu, In2.Cu | 4 spokes | 0.504 mm | 1.0180 mm² |
| R1 pad 2 | F.Cu | 3 spokes (the fourth would reach the +3V3 pad) | 0.300 mm | 0.36 mm² |
| TP2 | F.Cu | solid | — | 7.5454 mm² |
| 2 vias | F.Cu, In1.Cu, In2.Cu | solid | — | 0.2815 mm² each |
| R1 pad 1 (+3V3) | F.Cu | isolated | — | 0 |

Fills against KiCad's plot (symmetric difference / area): F.Cu 1540.8821 mm² on
both sides, 0.0000% (0.018% in the windows around pads); In1.Cu and In2.Cu
1749.0137 against 1749.0140 mm², 0.012%; B.Cu no fill on either side. Three
fills read (`fill:F.Cu:0` with 4 holes, `fill:In1.Cu:0` and `fill:In2.Cu:0`
with 1 each), all recovered from KiCad's fractured outlines.

Findings:

- Spokes and solid connections overlap their pads, they do not just touch, so
  the later union per net and layer joins them without a tolerance.
- The reader reads no thermal-relief metadata and needs none: the spoke geometry
  is in the fill, as the Phase 2 contract expected.
- Spokes are as narrow as 0.3 mm; experiment 8 already showed unrefined meshes up
  to 1.9% low because of 0.5 mm spokes. This is input to the meshing increment,
  not a reader issue.

Tests: `tests/test_thermal_relief_fixture.py` (3): fills against KiCad's plot,
whole board and around every pad; connection kind, spoke count and width, and
overlap for all 12 pad and via positions on zone layers; zone settings and fill
shapes. Deliberate one-line bugs, run with `PYTHONDONTWRITEBYTECODE=1`, each made
tests fail: pad rotation reversed (1), pad rotation ignored (1), IPC Y not
reversed (1), slits not split (all 3, stopped by the reader's own fill validity
check).

Not run or not covered: thermal reliefs on the private board (its fills were
already matched to KiCad in the fills increment, but pad connections were not
classified); zones with a hatched fill; KiCad 11.

Stopped after checking: no union, meshing, setup, CLI or solve.

## 2026-11-21 — Phase 2 small task: assemble one BoardModel (pin pads)

**Result / Recommendation: one `BoardModel` is built from an IPC-2581 export.
`ipc2581_board.read_ipc2581_board(path)` returns the stackup, nets, fills,
tracks, pad and via copper, drills and new `PinPad` records, and `BoardModel`'s
own checks pass on all three public boards and on Erekle's private board. On
the private board all 355 pin pads match KiCad's board file in component, pad
number, net, position (1 nm) and hole. Cloud workspace: 12 new tests pass; full
suite 632 passed, 4 skipped on Python 3.11.18, 3.13.18 and 3.14.6; Erekle's
machine 636 passed. Merged as PR #23. Recommendation: start copper cleanup
(increment 9), first settling the hole cutting radius and plating.**

Worked step by step with Erekle. Decisions by Erekle (2026-11-21):

- One `PinPad` per physical pad: the pad copper of one component and pin at one
  position, one feature per copper layer in stackup order. A pin with several
  pads gives several `PinPad`s (`pin:<component>.<pin>`, then `#2`...) sharing a
  pin key; `BoardModel` checks they share a net.
- Component, pin and position come from the copper reader (`pad_pins`, read from
  `PinRef` and `Location`), not parsed back out of feature IDs.
- A copper pad on a non-plated hole raises ("not supported yet"); revisit when a
  real board needs one. Neither tested board has one.

Also: the copper reader's unread copper raises (the model would be incomplete);
one pad's copper on different nets, or on two holes, raises. The model's
assumptions start with the board scope (holes not cut, no barrel, copper not
merged) and end with the non-copper layers not inspected.

Measured:

| Board | Layers | Nets | Features | Pins | On a hole | Drills |
| --- | --- | --- | --- | --- | --- | --- |
| arc (public) | 2 | 2 | 13 tracks, 4 pads | 4 | 0 | 0 |
| pad (public) | 4 | 14 | 18 tracks, 24 pads, 12 vias | 18 | 2 | 3 via, 2 pin |
| thermal (public) | 4 | 2 | 3 fills, 7 pads, 8 vias | 4 | 1 | 2 via, 1 pin |
| private | 4 | 141 | 11 fills, 785 tracks, 394 pads, 812 vias | 355 | 13 | 203 via, 13 pin, 2 mechanical |

Private board (scratch script `check_real_board.py`, not committed; output
pasted by Erekle): 342 single-layer pins and 13 through-hole pins with copper on
all four layers; 355 of 355 matched against KiCad, none only in ours, none only
in KiCad, no net or drill difference. The first run reported 22 pads only in
KiCad: all were paste-only apertures (8 `F.Paste`, 14 `B.Paste`) with an empty
pad number and no copper, which the script had counted as copper pads. The
script and the committed test's KiCad parser now skip pads with no copper layer,
counted by their own layers; no unnumbered pad with copper was found. 2
`np_thru_hole` pads are the 2 mechanical holes.

Tests: `tests/test_ipc2581_board.py` (12): every pin of the three public boards
against the `.kicad_pcb` (the test applies footprint position and rotation
itself, independent of the exporter; covers rotated and back-side footprints);
counts; the model holds everything the copper reader returned; assumptions; six
rejections built by changing one record of a real reader result (unread copper,
two nets, two holes, non-plated hole, a pin with two pads, those pads on two
nets). Deliberate one-line bugs, run with `PYTHONDONTWRITEBYTECODE=1`, each made
tests fail: pin position not transformed (3), pads grouped without position
(11), pin drill dropped (8), unread copper not checked (1), net mismatch not
checked (1), non-plated hole not checked (1), layers not in stackup order (2). A
first sabotage round was discarded: its restore step reverted the uncommitted
reader change, so it tested a broken mix.

Not run or not covered: copper pads on non-plated holes, pins with several pads
and blind or buried pin holes on a real export (none on these boards); KiCad 11.

Stopped after assembly: no union, hole cutting, meshing, setup, CLI or solve.

## 2026-11-21 — Decision: hole cutting radius and plating thickness

**Result / Recommendation: decided by Erekle (2026-11-21). KiCad's hole value
is the finished hole size; holes are cut from plane copper at the drilled radius,
finished radius plus plating; plating thickness is a required setup input with
no default.**

Evidence (web, 2026-11-21): the KiCad library conventions (KLC F7.6) state that
hole sizes are the finished size after plating, not the drill size; the KLC
change cites KiCad issue #7044, where KiCad itself was clarified the same way.
Fabricators expect finished hole sizes and drill plated holes larger to allow
for plating. The IPC-2581 `Hole@diameter` the drill reader records is the same
KiCad value. KiCad's design-rules label "via drill" is misleading: the value is
the finished hole.

Decisions, with d the exported hole diameter and t the plating thickness:

- The barrel is copper from radius d/2 (finished hole) to d/2 + t (drilled
  wall). The cleanup increment cuts plane copper (pad, via, fill, track) at
  radius d/2 + t; the contact model increment adds the barrel as that tube.
  This is what experiment 8 did (with t = 25 µm), so increment 9 starts from a
  tested choice.
- t comes from the setup file, from the fabricator's specification. It is
  required: a missing value raises with a message saying what is needed. There
  is no default, because a silent default changes via resistance unnoticed (18
  against 25 µm on a 0.2 mm via is about 35% more barrel resistance).
- Non-plated holes are cut at d/2 (no plating).

Not decided here: per-hole or per-size plating (one board-wide t for now),
drill tolerances (KiCad writes none), and annular-ring checks.

## 2026-11-22 — Decisions before copper cleanup (increment 9)

**Result / Recommendation: decided by Erekle (2026-11-22), before any cleanup
code. Overlapping copper of two nets raises; copper with no net is kept as its
own group outside the DC solve; cleanup splits each net and layer into pieces
with IDs, and an island on a simulated net is an error before solving
(detected in the contact model increment).**

1. **Copper of two different nets overlapping on one layer raises.** It is a
   short on the real board; merging per net would keep the two nets apart and
   give a wrong result silently. The error names both nets, the layer, the
   location and the overlap area. Net ties (footprints that join two nets on
   purpose) raise too, with a message that they are not supported yet; decide
   how to declare them when a real board needs one. The check uses a small area
   limit, not any contact: shapes only touching at a point do not raise.
   Outline approximation is 1 µm against clearances of about 0.1 mm or more, so
   correctly spaced copper never appears to overlap.
2. **Copper with no net (`net_id=None`) is kept as its own group**, merged per
   layer, outside the DC solve, and reported (count and area). Nothing is
   deleted: Phase 4 (thermal) needs it. If it overlaps net copper, cleanup
   raises as in 1, because the net it belongs to is unknown.
3. **Islands.** Cleanup splits each net's copper on each layer into its separate
   pieces, each with an ID and the features and holes it contains; several
   pieces per net and layer are normal (joined through vias and planes). Whether
   a piece is an island needs the barrels joining layers, so it is decided in
   the contact model increment (11): **a piece of a simulated net not connected
   to the rest of that net through copper, vias or pin barrels is an error, and
   nothing is simulated.** The error names the net, the layer, the location and
   the area. Pieces on nets that are not simulated are not checked. This also
   stops a run on a board with unrouted connections of a simulated net. The
   simulated nets are chosen in the setup file (increment 12).

Also from 2026-11-21: plated holes are cut at finished radius + plating
(plating a required input), non-plated holes at their radius. Until the setup
file exists, cleanup takes plating as a function argument.

## 2026-11-22 — Phase 2 small task: copper cleanup (pieces and cut holes)

**Result / Recommendation: copper cleanup works on real boards.
`copper_cleanup.clean_copper(board, plating_m)` merges each net's copper per
layer into separate pieces and cuts the holes. On Erekle's private board it
gives 610 pieces, the same count as KiCad's own plot on every layer, with area
within 0.07%; all 864 plated cuts are surrounded by copper; nothing raised.
Cloud workspace: 24 new tests pass; full suite 656 passed, 4 skipped on Python
3.11.18, 3.13.18 and 3.14.6; Erekle's machine 660 passed. Merged as PR #27.
Recommendation: start meshing (increment 10) from experiment 8's settings.**

Worked step by step with Erekle; decisions as logged on 2026-11-21 (hole radius,
plating) and 2026-11-22 (shorts, no-net copper, islands). Agreed in this step
(Erekle, 2026-11-22):

- Output: `CopperPiece` (ID `piece:<layer>:<net>:<k>`, `none` for no-net copper,
  largest first; one valid Polygon; the features and holes it holds), `CutHole`
  (drill, layer, radius, the pieces whose edge it is; a list, since a non-plated
  hole may split a track) and `CleanCopper` (pieces, cut holes, removed
  features, plating, assumptions).
- Hole outlines follow the 1 µm rule (vertices on the true circle), so a hole
  edge keeps at most 1 µm of extra copper; meshing may refine them.
- A plated hole breaking out of its own copper raises. A piece under 1 µm²
  raises. Nothing is simplified.
- Found on the private board: 37 track stubs (34 at vias, 3 at pin holes) lie
  entirely inside a drilled hole, where a route ends at the via or pin centre.
  The drill removes them, so they are listed in `CleanCopper.removed` with the
  hole; any other feature left in no piece raises. The public pad board has one.

Measured, plating 25 µm (the value experiment 8 used; the real value is a setup
input):

| Board | Pieces | Cut holes | No-net pieces | Drilled away | Against KiCad's plot |
| --- | --- | --- | --- | --- | --- |
| arc (public) | 2 | 0 | 0 | 0 | not compared (no SVG) |
| pad (public) | 30 | 20 | 1 | 1 | same pieces per layer, area within 0.36% |
| thermal (public) | 7 | 12 | 0 | 0 | same pieces per layer, area within 0.38% |
| private | 610 (199, 78, 186, 147 per layer) | 864 | 0 | 37 | same pieces per layer, area within 0.072% |

The reference is KiCad's SVG plot of each layer with the same holes cut here as
exact circles; area differences are curve approximation (our 1 µm polygons
against the plot's exact circles; largest on boards of small round pads). By
hand on the thermal board: a lone 0.6 mm via pad keeps π(0.3² − 0.175²) mm²
within 0.3%, TP1's ring π(1² − 0.525²) mm². Private board: 237 net-and-layer
groups, 182 of them one piece, the largest 138 pieces (pads joined through
vias; input to the island check); smallest piece 0.076 mm²; cleanup 2.5 s.

Tests: `tests/test_copper_cleanup.py` (24): pieces against KiCad's plot on the
pad and thermal boards; counts; every feature in a piece of its net or drilled
away, on all three public boards; cut radii by hand, doubled plating, a
non-plated 2 mm hole cut without plating; no-net group; assumptions; rejections
made by changing one record of the real thermal board (plating missing, zero,
negative, NaN, boolean or text; two nets overlapping; no-net copper on a fill;
breakout; a drilled wall reaching another net; hole copper on two nets; a
0.5 µm² piece; unknown hole plating); a stub inside a via drilled away and one
reaching past the wall kept. Deliberate one-line bugs, run with
`PYTHONDONTWRITEBYTECODE=1` on a committed tree, each made tests fail: plating
not added (6), plating added to non-plated holes (1, after adding the non-plated
test, which the first round showed was missing), holes not cut (3), overlap
check off (2), breakout check off (1), drilled-wall check off (1), tiny-piece
check off (1), hole-nets check off (1), pieces smallest first (1), drilled-away
features not recorded (3). Two changes cannot alter any result and were not
caught: piece membership ignoring the net (the overlap check already excludes
it) and partly-inside features counted as inside a hole (such a feature always
keeps copper in a piece). The raise for copper in no piece and not inside a
hole is a guard with no real case to test.

Not run or not covered: invalid pieces after Shapely operations (none produced);
net ties; non-plated holes on a real board; per-hole plating; KiCad 11.

Stopped after cleanup: no barrels, contacts, island check, meshing, setup, CLI or
solve.

## 2026-11-22 — Decisions before meshing (increment 10)

**Result / Recommendation: decided by Erekle (2026-11-22), before any meshing
code. The user does not choose element sizes: PDNLab meshes with default size
rules and proves each result with a refinement check, stopping with an error if
it does not settle.**

Explained with Erekle first: Gmsh makes the triangles; PDNLab gives it the
outlines and a size at every place, and Gmsh follows the smallest of the size
rules. A mesh behaves like a resistor network: where current flows evenly,
large triangles are exact; where it crowds (into a via, through a spoke, round a
corner), a coarse mesh spreads it over too much copper and the drop comes out
low (experiment 8: up to 1.9% low through 0.5 mm spokes without refinement).

Decisions:

1. **Refinement check on every run.** Solve with the default sizes; halve every
   size, keeping the pattern, and solve again; compare the drops at every
   source and load. Stop when the change is within the tolerance and report the
   result with the change. The tolerance is a user setting; the default is
   release gate 5 (10 µV + 1% of the drop). A limit on refinement rounds (about
   3): if the change has not settled by then, stop with an error naming where
   the change is largest, instead of running on. The loop needs solves, so it is
   built in increment 13; increment 10 provides meshing with size inputs and
   shows that halving the sizes really refines.
2. **Default size rules from experiment 8:** maximum element size 0.5 mm; at
   holes and through-hole pads the size starts at the hole outline's side and
   grows 0.15 mm per mm of distance up to the maximum; hole outlines at the 1 µm
   rule of the cleanup.
3. **Refinement also at thin copper:** thermal spokes and narrow necks get small
   elements too, so that current squeezing through them is resolved. How
   narrow, and how small, is settled with measurements in increment 10.

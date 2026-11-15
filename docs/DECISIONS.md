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

## 2026-11-15 — Experiment 7: padne reference run on a real net

**Result: padne ran; the raw comparison fails release gate 6 at every pin, and
the difference is located at the source pads. Downstream of the feed vias the
two tools agree within gate 6. KiPI was not run and the StickHub benchmark was
not run. Recommendation: add a terminal model for unsoldered contact pads to
PDNLab, and do not use padne's pad-centre voltages as a ±5 % reference.**

padne's all-in-one Linux executable installed and reports
`padne 0.1.dev1+gacab836b1`, the commit the directives were checked against on
14 November. Its SHA-256 was not recorded. The bundled KiCad prints
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
- Experiment 8 has no entry in this log yet. Its numbers above come from
  `_exp08/checks.json` and the per-layer result files.

**Open for the maintainer:**

- Terminal model for contact pads: per-pin choice between a soldered pad
  (equipotential) and a contact (a point or small area at a stated position).
- Whether the padne half of experiment 7 is closed with this result, and
  whether KiPI is still wanted.

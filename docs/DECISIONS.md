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

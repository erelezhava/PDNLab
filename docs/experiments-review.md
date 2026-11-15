# PDNLab DC: experiments done so far (review copy)

Compiled 2026-11-15 from: project doc `PLAN.md`, the repo log `docs/DECISIONS.md` (branch `exp7-reference-tools`, commit `ed8ac78`), project doc `claude/exp08-net-sim-handoff.md`, and the two exp08 chat sessions. Nothing here was re-run for this summary.

## Why you can't find them in PLAN.md

`PLAN.md` holds only the *definitions* of experiments 1 to 8 (section "Phase 0 experiments": name, pass criterion, effort). It has no results, and it does not mention work that was added later. Results live in three other places:

| What | Where | On `main`? |
| --- | --- | --- |
| Results of experiments 1 to 7 and the off-centre via check | `docs/DECISIONS.md` in the repo | No. Only on the PR branches; `main` still holds just the skeleton commit `55d0bff` |
| Extended experiment 6 audit on your real board, and all of experiment 8 | Your working tree in `~/Applications/pdnlab`, plus `claude/exp08-net-sim-handoff.md` | No. No `exp8` branch exists on GitHub as of 2026-11-15 |
| Result files for your board | `tests/test boards/test brd 1/_exp06/` and `_exp08/` (git-ignored) | Never, by design |

## Status at a glance

| # | Experiment | In PLAN.md | Status | Headline result | Limit in plan |
| --- | --- | --- | --- | --- | --- |
| 1 | Hand-made rectangle | Yes | Pass | Max relative error about 1.2e-11 | 1e-6 |
| 2 | Gmsh round trip | Yes | Pass | Errors 1.1e-12 or below | 1e-6 |
| 3 | Annular ring, tied rim | Yes | Pass | 0.005 % to 0.018 % coarse, smaller at h/2 | 0.5 % |
| 4 | Two rings and one barrel | Yes | Pass | 0.001 % to 0.010 % | 0.5 % |
| 5 | Current and power balance | Yes | Pass | 19 solves, 6 via-rim checks, residuals 1e-10 A / 1e-10 W or below | 1 nA, 1 nW + 1e-6 relative |
| A | Off-centre via (current crowding) | **No** | Pass | 0.007 % to 0.034 % coarse; near-half current share matches closed form to 1e-5 | 0.5 % |
| B | Solver input validation | **No** | Done | Unanchored islands, negative conductance, empty terminals now raise errors | n/a |
| 6 | IPC-2581 export audit, StickHub demo | Yes | Partial, provisional | Fills 11/11, stackup, pins 266/266, vias 87/87 match | Data present |
| 6b | Same audit on your 4-layer board, plus tracks and pad holes | **No** | Run, not logged in repo | Four original gates pass; tracks 785/785; pad holes 15/15; pad copper result not recorded in the docs | n/a |
| 7 | padne and KiPI reference runs | Yes | **Blocked** | Nothing run. Benchmark and directive files prepared only | Reference voltages recorded |
| 8 | Real copper through cleanup and Gmsh | Yes | Run, went well past the planned scope | +3V3 net on all layers meshed and solved; max drop 3.025 mV at 1.1 A | "It meshes" |
| 8a | Per-via currents and conservation | **No** | Done | 28 vias, 16 carry current; rims balance to about 1e-12 A | n/a |
| 8b | Local mesh refinement at holes and through-hole pads | **No** | Done | J1/J5 numbers moved +1.4 % to +1.9 %; J4 moved +0.25 % | n/a |
| 8c | Mesh convergence on the real net | **No** | Done | 42 of 42 gates pass; at most 0.37 % change between refined levels | n/a |

Rows marked **No** are the ones missing from PLAN.md.

## Experiments 1 to 5: solver core on ideal shapes

All five passed. Detail and tables are in `docs/DECISIONS.md`.

- **1, rectangle:** R against ρL/(Wt). Six script cases, 11 tests.
- **2, Gmsh round trip:** `.geo` → external `gmsh` → MSH 4.1 → meshio → tags → solve. Four cases, 32 to 1,084 nodes.
- **3, annular ring:** R against ρ·ln(b/a)/(2πt). Three geometries, two mesh levels each; error shrinks on refinement in all three.
- **4, two rings and barrel:** ring + barrel + ring. Barrel voltage drop is checked separately, so a shorted barrel cannot hide.
- **5, balances:** the release 0.1 tolerances for current, per-rim KCL and power are applied to experiments 1 to 4.

Review points:
- In experiment 5, the current and rim checks are residuals of the same matrix that was solved. Only the power check is independent of the assembly.
- Experiments 3 and 4 are axisymmetric. That is why check A was added.
- None of this shows the tied-rim via model matches a real plated via. That needs a 3D coupon or measurement.

## A and B: added during review, not in the plan

- **A, off-centre via.** Hole moved off-centre in a circular sheet, compared with R = ρ/(2πt)·acosh((a² + b² − d²)/(2ab)). Three cases. For a hole 0.15 mm from the edge, 93.5 % of the current leaves through the near half of the outer edge; solved and closed-form shares agree to 1e-5. Derivation: `docs/off-centre-via-reference.md`.
- **B, input validation.** The low-level solver rejects unanchored components, nonfinite inputs and malformed meshes. Floating-copper classification, as described in the plan, is not implemented.

## Experiment 6: IPC-2581 export

**Logged part (StickHub demo, 2 layers).** Exported by KiCad 10.0.8; the board file was saved by a 9.99 development build. Fills, stackup thickness, pin-to-net and via holes all match. Plating thickness is not in the export and stays a setup input. Importer route: IPC-2581 is the candidate, **not settled**.

**6b, your board (MAIN_MCU_STM32H743, 4 layers, 1.0192 mm, 70 footprints).** Run on 2026-11-14, not yet in `docs/DECISIONS.md`.
- First true KiCad 10 save the tool saw (file format 20260206). The parser crashed on inline net names (`KeyError: 'GND'`); fixed in `ipc2581_inspect.py`.
- Board content: 355 pad-to-net mappings, 785 tracks (no arcs), 203 through vias (0.2, 0.3, 0.35 mm), 11 fill polygons.
- You ran the export; the four original gates pass.
- New audit `ipc2581_geometry.py`: tracks 785 of 785, pad holes 15 of 15. Pad copper first failed because of a units bug in the new code. The bug was fixed, but the final pad count is not written in any doc I can read. Check `_exp06/results.json`.

Still not audited: track arcs, custom pads, slots, blind and buried vias, which inner layers a via actually contacts.

## Experiment 7: padne and KiPI

Blocked. Neither tool has been installed or run, so there are no reference voltages. Prepared: a benchmark on StickHub (5 V at J2.1, 0.5 A at J7.1), a padne directive file checked against padne commit `acab836`, and install steps in `docs/experiment-07-setup.md`. Your action: install both tools.

## Experiment 8: real net (+3V3 on your board)

The plan asked for one net on one layer to mesh. What was built is a full DC solve of the +3V3 net across all its layers.

**Model.** Per-layer copper polygons (shapely), meshed separately by Gmsh. Via holes cut at drill/2 + 25 µm. One rim node per contacted layer, one barrel resistor between consecutive contacted layers. Pins are equipotential. Output is a transfer matrix Z (drop at pin p per ampere at pin k), so any load pattern is a matrix product.

**Assumptions to confirm.** ρ = 1.724e-8 Ω·m; plating 25 µm; copper F/B 35 µm, In1/In2 15.2 µm; saved zone fill is current; 100 mA at each of 11 IC supply pins. The 100 mA is a placeholder; you said real loads are much lower. Drop scales linearly with current.

**Results (refined mesh: h = 0.5 mm, 96-sided holes, growth 0.15).**

| Source | Max drop | Self-Z at load pins | Change from first run |
| --- | --- | --- | --- |
| J4 | 3.025 mV | 4.44 to 5.85 mΩ | +0.25 %, +0.5 % |
| J1 | 2.681 mV | 4.22 to 5.62 mΩ | +1.9 %, +1.45 % |
| J5 | 2.578 mV | 4.18 to 5.57 mΩ | +1.9 %, +1.4 % |

The first-run J1 and J5 numbers are superseded: those pins join In2.Cu through three 0.5 mm thermal-relief spokes that the first mesh covered with one or two elements.

**8a, per-via currents (J4 source).**
- 28 vias; 16 carry current, 12 end on passive pads.
- Largest: v27 337.6 mA, v3 315.7 mA, v13 276.5 mA, v18 170.3 mA. Sum 1.100 A.
- Highest density, v27: barrel 13.2 A/mm²; hole edge on In2.Cu 20.2 A/mm² average; hole edge on B.Cu 38.2 A/mm² peak. The layer edge is the smallest cross-section, not the barrel.
- Every rim balances to about 1e-12 A.

**8b, local refinement.** Gmsh size field at via holes and at through-hole pads that feed copper. Two closed-form tests back it:
- one via joining two strips: FE error −1.7e-3 (first mesh), −7.8e-4 (n = 48), −3.0e-4 (h = 0.25, n = 96);
- pad feeding a strip through one 0.5 mm spoke: −3.6 % (first mesh), −0.34 % (n = 48), +0.01 % (n = 96).

**8c, convergence.** Six mesh levels. Per-via current changes at most 0.7 mA (0.2 %) between refined levels; max drop and self-Z at most 0.37 %. 42 of 42 gates pass.

**Tests.** 179 passed, 2 skipped, in the cloud sandbox (Python 3.13, Gmsh 4.12.1). As of the handoff note, the suite had not been run on your machine.

**Known limits.**
- Not compared with measurement or with another tool.
- SMD pads joined to a zone by thermal spokes are not refined (this net has none).
- Via in pad is rejected. Through-hole pins are ideal leads with no barrel resistance.
- Hole-edge peak density assumes an equipotential rim.
- Only the zone's `thermal_gap` is read, not per-pad overrides.

## What to check when you review

1. **Pass criteria.** Do experiments 6 and 8 count as passed for you? 6 is partial; 8 exceeded its scope but is unvalidated.
2. **Assumptions in experiment 8.** Real load currents, which connector feeds +3V3, plating thickness.
3. **Plan drift.** Rows A, B, 6b, 8a to 8c are not in PLAN.md. Either add them to the plan or keep this file as the record.
4. **Git.** PRs 1 to 4 are unmerged. The 6b and 8 work needs `uv add shapely`, `uv run pytest`, then a commit on a new `exp8-real-net` branch.
5. **Open blockers.** Experiment 7 (install padne and KiPI) is the only route to an independent cross-check before release 0.1 gate 6.

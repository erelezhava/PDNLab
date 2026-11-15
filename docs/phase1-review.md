# Phase 1 solver-core review

Initial code review: 2026-11-15 at local commit
`e9453574361cac7716a1d1ca766f9154eaef6401` on `phase1-solver-core`.

**Result / Recommendation: Erekle accepted the documented Phase 1 synthetic
solver scope on 2026-11-15. PR #8 is merged and CI on the merged main commit
passed. Recommendation: carry the limits below into Phase 2; acceptance does
not establish production-import or physical-model validation.**

## Requirements and evidence

| Requirement | Implementation / evidence | Assessment |
| --- | --- | --- |
| FEM sheets, electrical ties, positive lumped/barrel resistors | Existing `fem.py` / `vias.py`; named composition in `network.py`; strip, annular and branching-via tests | Covered for supported synthetic inputs |
| Fixed voltages and explicit current sinks | Prepared source membership, changing voltages/currents, tied or explicitly weighted terminals; malformed/unknown/floating inputs rejected | Covered for sources to a reference; arbitrary floating voltage sources are outside the MVP |
| Stable package entry points | `solver.prepare_fixed_voltages`, `network.Network`, `contacts.select_area_contact`; existing one-shot FEM calls preserved | Available and documented; no external API compatibility guarantee beyond this development version |
| Analytic accuracy at 0.5%, with tighter existing limits | Full suite and analytic examples recorded in `DECISIONS.md`; strip/network tests retain 1e-6 relative limits where applicable | Local evidence and merged-main CI pass; maintainer gate accepted |
| Mesh convergence and independent conservation/power | Annuli, contacts and per-layer branching KCL; heat from triangle gradients and resistor drops; deliberate omitted-heat/wrong-current tests | Covered locally; self-consistency is not physical validation |
| Prepare/factor once, solve specified currents directly | Prepared snapshot, LU-reuse tests, no default transfer extraction | Covered in package interfaces |
| Optional batched transfer extraction | Actual RHS batch-shape and direct/transfer tests; named wrapper for equipotential terminals | Covered; dense output still scales with requested ports |
| Sparse LU initially, future backend possible | Backend-specific factors remain private behind prepared solves | LU implemented; iterative backend not implemented or benchmarked |
| Timing/memory instrumentation | Sheet/contact/composition/preparation/factor/solve timings, sparse counts and RHS bytes; synthetic probe | Scoped measurements only; not whole-model memory or peak RSS |
| Avoid retaining refinement levels unnecessarily | New convergence examples/tests release each model/factorization before constructing the next, retaining scalar summaries | Covered for the new synthetic workflow; historical real-board orchestration is unchanged |
| Explicit finite-area contacts | Conforming equipotential polygons and integrated uniform-current patches; mean-voltage/heat references, position and hole tests | Covered under the documented idealizations and mesh restrictions |
| Preserve Phase 0 evidence and private-board boundary | Historical scripts retained; only the protective `.gitignore` is tracked under `tests/test boards/` | No private board content is tracked or added by these increments |

## Measured validation

The latest full run, before the contact commit, was **336 passed, no skips,
56.12 s**. Focused contact/composition run: **57 passed, 3.97 s**. These are
the previous locally measured results, not new runs for this review. The
working tree at review start was clean and the contact commit contains the
expected eight files. `git diff --check` was run during this review and passes.

Results, geometry/material assumptions and limits are recorded separately in
the four phase-1 entries of `DECISIONS.md`. The new standalone probes are:

```sh
.venv/bin/python experiments/phase1_solver_benchmark.py
.venv/bin/python experiments/phase1_network_cases.py
.venv/bin/python experiments/phase1_contact_cases.py
```

No new numerical solve, hardware measurement or private-board run was needed
for this documentation/code-inspection review. It does not constitute an
independent implementation or physical-model audit.

## Acceptance recorded on 2026-11-15

- Erekle merged [PR #8](https://github.com/erelezhava/PDNLab/pull/8) into
  `main`, merge commit `a8dadf556bbabb91fa99e039cf5310ab5ef4519b`.
- The [tests workflow on merged main](https://github.com/erelezhava/PDNLab/actions/runs/43740365125)
  completed successfully. Its `pytest` job and `Run pytest -v` step both report
  success; the Gmsh install step also succeeded. The workflow uses Python 3.11.
- Erekle explicitly requested recording Phase 1 acceptance and the passing
  CI result. This closes the documented synthetic solver-core phase, with the
  carry-forward limits below retained.

The earlier missing-commit/CI-pending state is superseded by this merged-main
evidence. Remote job/step conclusions were inspected, but detailed remote test
counts were not retrieved; the local counts above are not CI counts. KiCad's
optional native integration may skip on CI, which does not install KiCad.
No numerical tests were rerun for this acceptance documentation update.

## Carry-forward limits

- Production IPC-2581 parsing, neutral board/setup integration, real-board
  orchestration migration, automatic contact-boundary meshing and floating
  copper classification belong to Phase 2 or later review.
- Terminals currently select one sheet and have disjoint FEM support.
  Equipotential area boundaries must fit the mesh; uniform-current patches
  are prescribed sink distributions, not fixed-voltage electrodes. Contact
  resistivity and 3D interface effects are not inferred.
- A mathematical point's local voltage is mesh-dependent. The diagnostic
  demonstrates this; it is not a physical contact-resistance prediction.
- Large-board time/memory scaling, peak RSS and an iterative backend remain
  unmeasured. Sparse-array counts exclude native workspace and parts of the
  composition model. No real-board speedup is claimed.
- Full-3D comparison, hardware measurements, arbitrary floating voltage
  sources, remote-sense regulators, thermal and viewer work remain outside
  the completed synthetic scope.

Recommendation: carry these limits into Phase 2 planning; do not treat Phase 1
acceptance as completion of production ingestion or additional physics.

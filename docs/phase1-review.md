# Phase 1 solver-core review

Reviewed 2026-11-15 at local commit
`e9453574361cac7716a1d1ca766f9154eaef6401` on `phase1-solver-core`.

**Result / Recommendation: the implemented synthetic solver scope is ready
for PR review; no additional coding blocker was identified in this scoped
review. Recommendation: run PR CI, resolve any findings and ask Erekle to
accept the phase-1 gate. Phase 1 is not declared closed by this document.**

## Requirements and evidence

| Requirement | Implementation / evidence | Assessment |
| --- | --- | --- |
| FEM sheets, electrical ties, positive lumped/barrel resistors | Existing `fem.py` / `vias.py`; named composition in `network.py`; strip, annular and branching-via tests | Covered for supported synthetic inputs |
| Fixed voltages and explicit current sinks | Prepared source membership, changing voltages/currents, tied or explicitly weighted terminals; malformed/unknown/floating inputs rejected | Covered for sources to a reference; arbitrary floating voltage sources are outside the MVP |
| Stable package entry points | `solver.prepare_fixed_voltages`, `network.Network`, `contacts.select_area_contact`; existing one-shot FEM calls preserved | Available and documented; no external API compatibility guarantee beyond this development version |
| Analytic accuracy at 0.5%, with tighter existing limits | Full suite and analytic examples recorded in `DECISIONS.md`; strip/network tests retain 1e-6 relative limits where applicable | Passes locally; CI acceptance remains outstanding |
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

## Remaining acceptance steps

1. Commit this review documentation and push `phase1-solver-core`.
2. Open a PR targeting `main`. The current workflow runs on PRs and pushes to
   `main`; pushing only the feature branch will not trigger it.
3. Review CI's Python 3.11 result and analytic gates. Do not require the same
   full local test count: CI currently omits KiCad and its optional native
   integration may skip. Analytic/Gmsh checks must run.
4. Erekle reviews and chooses whether to accept/merge the phase-1 scope.
   No automatic merge is requested or performed.

GitHub's check-runs lookup for the reviewed SHA returned “No commit found”
during this review. Remote CI therefore has not been verified for this commit.

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

Recommendation: carry these limits into Phase 2 planning, without expanding
the phase-1 PR solely to implement production ingestion or additional physics.

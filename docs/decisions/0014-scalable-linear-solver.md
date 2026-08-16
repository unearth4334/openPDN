# ADR-0014: A scalable iterative linear-solver backend beside the direct solve

## Status

Accepted. Extends ADR-0010 §5, which chose the direct solve and explicitly
left this door open ("Iterative/PETSc/Elmer backends remain possible behind
the same `ElectricalSolver` contract"). The direct path is not replaced.

## Context

`solve.py` factorises the Dirichlet-reduced system with SciPy's SuperLU. That
is the right default: deterministic, no tuning, and the factorisation is
reused across excitations. It is also the binding constraint on problem size —
a sparse LU of a 2-D problem fills in badly, and its memory grows far faster
than the DOF count. Reference meshes are intended to reach millions of DOFs,
where direct factorisation stops fitting in a worker.

Two facts about the existing formulation decide which iterative method applies.
Dirichlet conditions are imposed by **row/column elimination**, not by penalty
or Lagrange multipliers: `solve.py` slices the free DOFs and solves
`K_ff v = rhs - K_fd v_d`. Elimination preserves symmetry, and the sheet
conductance operator is positive definite on a connected, driven component.
So `K_ff` is genuinely SPD — the exact hypothesis conjugate gradients requires.
Had BCs been applied by penalty or multipliers, this ADR would look different;
they were not, so CG is available without changing the boundary-condition
scheme.

Library choice is constrained by licensing, the same way ADR-0010's mesher
choice was. This is an Apache-2.0 project that already rejected Triangle
(non-commercial) and Gmsh (GPL) as hard dependencies.

## Decision

1. **Method**: preconditioned conjugate gradients on the SPD reduced system,
   with algebraic multigrid as the preconditioner. AMG is chosen because the
   operator is a scalar elliptic diffusion problem on an unstructured mesh with
   strongly varying coefficients — the problem class AMG was designed for and
   where its near-mesh-independent iteration count actually materialises.

2. **Library**: PETSc via `petsc4py`, with hypre BoomerAMG as the
   preconditioner. PETSc is BSD-2-Clause and hypre is dual Apache-2.0/MIT, both
   compatible with this project's licence discipline. **MUMPS is rejected** as
   a default backend: its licence (CeCILL-C) carries copyleft-style obligations
   that fail the same test that excluded Triangle and Gmsh. Licence terms are
   re-verified at the version actually pinned, not assumed from this record.

3. **`petsc4py` is an optional dependency, never a hard runtime requirement.**
   The base image installs pure Python wheels today with no `apt` layer at all;
   PETSc generally wants system BLAS/LAPACK/MPI and materially enlarges the
   image. The default published image therefore stays as it is, and a separate
   image variant carries the scalable stack. If Phase 4 measures the size
   increase as modest, collapsing back to a single image is a follow-up
   amendment to this ADR.

4. **Absent capability is refused, never silently degraded.** If a job requires
   an iterative solve and the backend is unavailable, the job fails with a
   clear reason. It must not fall back to a coarser mesh, a looser tolerance,
   or a direct solve that will exhaust memory. This is the same rule the DOF
   budget already follows (ADR-0011 §6): refuse rather than quietly deliver
   something weaker than was asked for.

5. **Backend selection is `Auto` | `Direct` | `Iterative`**, exposed as an
   advanced setting and resolved server-side. `Auto` picks direct below a
   calibrated DOF/memory threshold — where determinism and factorisation reuse
   win — and iterative above it. The threshold is measured in Phase 4, not
   guessed here.

6. **Linear-algebra error must sit well below discretisation error.** The
   iterative tolerance is derived from the run's target FEM error rather than
   being a fixed constant, at a ratio of at most `0.05`, so that the linear
   solve is never the accuracy-limiting step. The ratio is validated in Phase 4.
   **A solve that terminates on maximum iterations is a failure, not a
   result**, and is reported as `NUMERICAL_FAILURE` (ADR-0015).

7. **Reported diagnostics**: backend, preconditioner, iteration count,
   relative and absolute residual, and convergence reason, on every solve.
   ADR-0010 §6's rule stands unchanged and is worth restating because an
   iterative solver makes it easier to violate: **the linear residual is not
   accuracy**. It bounds how well the discrete system was solved, never how
   well the discrete system approximates the physics.

8. **Direct-versus-iterative cross-validation is a release gate.** On every
   analytical validation case small enough for both, the two backends must
   agree on terminal resistance, terminal voltages, integrated power and field
   norms to within a stated tolerance. A scalable backend that has not been
   cross-checked against the direct one is not trusted for Reference work.

## Consequences

* A second published image variant, and CI that builds it. The default deploy
  path (GHCR sha tag to Portainer) is unchanged for the lean image.
* `SolverCapabilities` gains backend availability so the API can report
  honestly, before queueing, that an iterative-only job cannot run on this
  deployment.
* Determinism weakens in a specific, disclosable way: a direct factorisation is
  bit-reproducible, an iterative solve converges to within a tolerance and its
  iteration count can vary with library version and threading. Results record
  the backend and tolerance so a number can always be traced to how it was
  produced, and the direct path remains available when exact reproducibility
  matters more than size.
* AMG setup cost is not negligible and is paid per solve. For the adaptive loop
  (ADR-0013), which re-meshes and re-assembles every pass, there is no
  opportunity to amortise a preconditioner across passes — only across the
  primal/adjoint pair within one pass.
* Multi-process MPI parallelism is explicitly **out of scope**. Workers are
  isolated single processes (ADR-0011); PETSc is used in serial (or threaded)
  mode. Distributing one solve across processes would change the worker model
  and needs its own decision.

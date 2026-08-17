# Reference tier — final development report

Status of the Reference accuracy milestone (ADR-0012 … ADR-0015) at the end
of implementation, 2026-08-16. Every number below was measured on this
codebase; where something could not be measured, that is stated instead of
estimated. The validation suite (`tests/validation/`) reproduces the core
tables.

## Element formulation

P1 (three-node linear triangle, closed-form stiffness) and P2 (six-node
quadratic triangle, straight-sided, degree-2 three-point quadrature that is
*exact* for the stiffness integrand). P2 midpoint nodes append after the
vertex block so the P1 node set is the P2 prefix; midpoints inside terminal
or via contact regions collapse into them, and so does any midpoint whose two
endpoints collapsed — omitting that second clause was measured to make P2
*worse* than P1 (9.0e-3 vs 6.8e-3 straight-trace error) by shortening the
equipotential contact region.

Validation: method of manufactured solutions on harmonic exact fields.
Observed convergence order **P1 = 1.88, P2 = 3.89–3.91** (nodal norm); P2
reproduces a harmonic quadratic to 1.3e-16 where P1 gives 9.1e-4 on an
irregular mesh. A uniform grid is *not* a neutral test bed: on it P1
coincides with the five-point FD Laplacian and is nodally exact for harmonic
quadratics too, so the element tests jitter the mesh.

## Adaptive estimator and marking

Residual edge flux jump, integrated exactly along each edge (the jump varies
linearly along an edge at P2; single-point sampling misreports the error).
The interior residual term vanishes identically — zero source, per-element
constant sheet conductance. Recovered-gradient (ZZ) smoothing was rejected
as the driver: at a genuine sheet-conductance jump the true flux is
discontinuous and recovery reports error where the answer is right.

Marking: Dörfler bulk marking, θ = 0.5 default, deterministic tie-breaks.
Refinement re-meshes the whole region from an error-driven sizing field —
the mesher has no edge adjacency or hanging-node support, so local
subdivision would mean writing a second mesher. Consequence: successive
meshes are **non-nested**, re-meshing alone moves resistance by parts per
thousand, and every convergence claim is written against bands.

Goal orientation (dual weighting) is implemented and **off by default**, on
measurement: for a resistance study read at the driven terminals the problem
is self-adjoint and the dual is exactly −1× the primal (ratio −1.000000,
σ = 0.0 over 737 nodes), so weighting reduces to squaring the indicator. It
earns its extra solve only when the functional and excitation differ.

## Convergence targets and stopping

Stopping is a conjunction: QoI relative change within target **and** the
global error estimate itself stabilised (its own pass-over-pass relative
change within target) for `confirmations` consecutive passes **and**
current imbalance within 1e-6 and power mismatch within 1e-3 (ADR-0010 §6's
error threshold, not its warning one). The estimator criterion is not
decorative: a first implementation stopping on QoI change alone declared
convergence on two non-nested meshes that happened to agree.

Both the estimator criterion and the power-mismatch gate were redesigned on
the same real production job (a 392-via board, 37 nets, 180 terminals, a
16-terminal source group) that reported `RESOURCE_LIMITED` after exhausting
its full 16-pass ceiling despite the answer having visibly settled.

The estimator criterion originally required the estimator to *halve from its
first, coarsest pass* -- on that board the global estimator plateaued at
~55% of its starting value from pass 6 onward (goal-oriented marking did not
change this) while the QoI had settled to a relative change of 1e-11 by pass
12. A singular contribution (a via annulus, a reentrant corner) can dominate
the global RSS estimator and cap how far it falls without the QoI being
affected at all. The estimator criterion now asks whether the estimator has
*itself stopped moving*, not whether it fell by an arbitrary multiple --
true whether or not a singular region bounds it above the old target.

The power-mismatch gate originally reused ADR-0010 §6's *warning* threshold
(1e-6) as a hard convergence requirement rather than its *error* threshold
(1e-3). On the same board power mismatch settled at 1.0e-5 to 1.4e-5 across
all eleven passes of a corrected run -- past the old gate, well inside the
error threshold, and not something further refinement was going to remove
(a lumped via-barrel accounting characteristic of a via-dense board). The
published result still carries the `numerics.power_mismatch` warning
diagnostic either way; only the hard gate moved. Current imbalance stayed at
the tighter warning threshold: the same board measured 3e-8 to 4e-8 there,
25x inside even that gate, so nothing observed justified loosening it too.
With both fixes the board converges at pass 10 (11 total passes) instead of
exhausting the 16-pass ceiling.

Per-quantity verdicts: resistance, total loss and J99 converge; sampled peak
|J| is tagged singular, reported, and never converged on — its failure to
settle downgrades a run to CONVERGED_WITH_MODEL_LIMITATIONS rather than
condemning it. Richardson extrapolation publishes only after monotonicity,
shrinking steps and a plausible fitted order are verified; oscillating
sequences are refused.

## Accuracy achieved (validation cases)

Reference value for `plane_neck_plane_board`: **6.882549 mΩ** (uniform P2,
571,557 DOFs). An earlier reference taken from the finest uniform *P1* mesh
was itself 0.5 % off and silently corrupted every error figure measured
against it — the tell was the two orders disagreeing by more than their own
apparent convergence.

| method | DOFs | relative error |
| --- | --- | --- |
| uniform P1 | 2,465 | 9.98e-3 |
| uniform P1 | 35,976 | 5.28e-3 |
| uniform P1 | 143,213 | 1.98e-3 |
| adaptive P1 | 513 | 3.73e-3 |
| **adaptive P2** | **1,388** | **1.07e-3** |

Adaptive P2 beats uniform P1 at roughly **1/100th** of the DOFs. Honest
counter-case, kept in the suite: on `series_widths_board`, where resistance
is spread along the whole path, adaptivity does **not** beat uniform —
adaptivity pays when error and copper are in different places.

Contact models: a distributed contact converges (0.4272 mΩ) while a point
contact diverges by a constant +0.0587 mΩ per mesh halving, matching the
analytical 2-D spreading rate ln(2)/(2πGs) = 0.0543 mΩ to 8 %.

## Largest successful solve

Uniform P2 on `plane_neck_plane_board`, direct (SuperLU) backend:

    DOFs        2,279,489
    triangles   1,213,992
    matrix NNZ  26,186,449
    R           6.884182 mΩ   (2.4e-4 from the stored reference)
    imbalance   8.5e-8        power mismatch 8.5e-8
    wall        256.5 s       peak RSS 11.25 GiB

The ≥ 2M-DOF target of the milestone spec is met, stably, with conservation
intact. Peak RSS (~5.3 KB/DOF) re-calibrated `BYTES_PER_DOF` to 5000: peak
process memory, not factor storage, is what the OOM killer acts on.

## Linear solver

Direct: SuperLU with iterative refinement, the default and the reference.
Iterative: CG with **smoothed-aggregation AMG** (pyamg — MIT, wheel-only;
petsc4py remains unbuildable here, and moving to PETSc + hypre later is a
preconditioner swap behind the same report fields). Jacobi remains the
dependency-free fallback, and without AMG `AUTO` refuses to route large
jobs to it — Jacobi-CG was measured unable to converge at 2.24M DOFs
(5,000 iterations exhausted at residual 4.2e-3).

Measured, `plane_neck_plane_board`, matched answers to better than 1e-9:

    DOFs        direct       AMG-CG      AMG iterations
    35,976      0.10 s       0.91 s           13
    143,237     1.53 s       2.10 s           29
    571,557    19.02 s       3.02 s           39
    2,279,489  256.5 s      12.9 s            42   (3.16 GiB vs 11.25 GiB)

Iteration count is near mesh-independent — the property the backend was
designed around — and the wall-time curves cross between 143k and 571k
DOFs, so `AUTO` switches to iterative at 200k when AMG is available.
Cross-validation (release gate): direct and iterative agree on terminal
resistance, fields, power and conservation across every size checked.

## Current imbalance / energy imbalance

Direct solves: ~1e-13 to 1e-10 across the suite; 8.5e-8 at 2.28M DOFs.
Warning threshold 1e-6, error 1e-3 (ADR-0010 §6): both are reported as
diagnostics at every adaptive generation as before, but only current
imbalance is *gated* at the warning line — power mismatch gates at the error
line instead, per the production finding above.

## Sub-tiers

`ReferenceTier` presets resolve to policy numbers at draft time and are then
forgotten (the spec and signature carry only resolved values, per ADR-0011).
Measured on `plane_neck_plane_board` from the coarse Reference start, after
the boundary-sampling fix below:

    tier     target    passes  DOF ceiling  confirmations   converges at
    low      1 %       ≤ 6     500 k        1               ~4 passes, err ~9e-4
    medium   0.1 %     ≤ 12    2 M          2               ~7-10 passes, err ~2e-4
    high     0.03 %    ≤ 16    8 M          3               ~9-12 passes, err ~8e-5

Calibrating the tiers exposed and fixed a mesher defect: boundary sampling
consulted the refinement field only at pilot points (spaced at half the
maximum element size), so demands finer than that were silently ignored --
the adaptive loop froze at an identical mesh for seventeen consecutive
generations while `dQoI` collapsed to 1e-8, self-consistency masquerading as
convergence. With the per-step fix, the loop reaches the stored reference's
own uncertainty band (~2e-4) by ~5,000 DOFs, and `θ` is calibrated at 0.7.

## Orchestration

Reference jobs freeze a `ReferencePolicy` (never a mesh), hashed into the
signature; schema 3 with migration from schema 1/2. Scheduling: Reference is
a separate priority class behind interactive work; memory-aware admission
defers (with an attempt-count-neutral `release_claim`) rather than
oversubscribing. Checkpointing: every pass boundary persists (~JSON, seeds +
metrics only — determinism makes mesh/solution storage unnecessary), a
requeued run resumes bit-for-bit (tested), SIGTERM/cancel stops at the next
boundary and publishes a partial result labelled NOT_CONVERGED with the job
CANCELLED, and the loop self-limits to 0.8× the orchestrator's hard timeout
so a long run is never SIGKILLed with total loss.

## Geometry and model limitations

Unchanged from ADR-0010 and stated on results rather than hidden: no
constrained triangulation (boundary fidelity is sampling-based), 2.5-D sheet
conduction (no vertical current spreading, no in-barrel crowding), lumped
via segments with exact annuli, assumed plating surfaces as a diagnostic.
Numerical convergence never implies physical-input accuracy; the four error
sources (geometry, discretisation, linear algebra, model-form) are reported
separately.

## Not done, and why

* **PETSc + hypre specifically** — the environment cannot build petsc4py.
  The AMG requirement itself is met through pyamg; a later swap to hypre is
  contained behind the preconditioner report field.
* **Cross-solver validation against ElmerFEM** (spec §56) — no external FEM
  package installable here. The independent checks that stand in its place:
  manufactured solutions with observed convergence order, analytical
  resistance/spreading formulas, and direct-vs-iterative backend agreement.
  A `reference-validation` workflow against Elmer remains future work and
  must not enter ordinary CI.
* **Mesh and error-indicator field overlays in the viewer** (spec §48–49) —
  the result panel shows the quality state, a convergence plot, the
  per-generation table and per-quantity verdicts; rendering the adaptive
  mesh itself (and per-element indicators) as viewport overlays needs
  artifact-schema and scene-model additions and remains future work.

The geometry-precision floor (spec §15) is implemented: refinement seeds
clamp at 2 µm — the scale of the importer's arc tessellation — each
generation records how many seeds the floor bit, and the CLI and result
panel say when further accuracy is limited by source fidelity rather than
compute.

## Recommended next numerical improvement

Adaptive-mesh and error-indicator viewport overlays, or the PETSc + hypre
swap if multi-million-DOF Reference work becomes routine — with AMG landed,
nothing else in this report is blocked on numerics; the remaining items are
visualisation and an external cross-check.

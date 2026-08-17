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

Stopping is a conjunction: QoI relative change within target for
`confirmations` consecutive passes **and** the estimator fallen by
`required_error_reduction` from the first pass **and** current and energy
conservation within 1e-6. The estimator criterion is not decorative: a first
implementation stopping on QoI change alone declared convergence on two
non-nested meshes that happened to agree.

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
Iterative: SciPy CG with **Jacobi** preconditioning — *not* the PETSc + hypre
AMG that ADR-0014 chose, because petsc4py could not be built in this
environment (no system PETSc, no network) and pyamg is absent. The
abstraction, derived tolerance (≤ 0.05 × discretisation target), full
diagnostics and refusal-on-max-iterations are all in place for AMG to slot
into.

Cross-validation (release gate): direct and iterative agree on terminal
resistance to 1e-14 relative across 287–35,976 DOFs, and on the whole field,
power and conservation likewise.

Measured limits, recorded rather than smoothed: Jacobi-CG iterations grow as
√DOFs (112 → 940 over 287 → 35,976); at 2,244,650 DOFs it exhausted 5,000
iterations at residual 4.2e-3 and refused. An earlier `AUTO` threshold of
500k therefore routed feasible jobs to a backend that predictably could not
converge them; `AUTO` now means direct until an AMG preconditioner exists,
and `iterative` is an explicit memory-bound opt-in.

## Current imbalance / energy imbalance

Direct solves: ~1e-13 to 1e-10 across the suite; 8.5e-8 at 2.28M DOFs.
Warning threshold 1e-6, error 1e-3 (ADR-0010 §6), unchanged and enforced at
every adaptive generation.

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

* **PETSc + hypre AMG** — environment cannot build it. The measured √DOF
  iteration growth of Jacobi-CG is the empirical case for it.
* **Cross-solver validation against ElmerFEM** (spec §56) — no external FEM
  package installable here. The independent checks that stand in its place:
  manufactured solutions with observed convergence order, analytical
  resistance/spreading formulas, and direct-vs-iterative backend agreement.
  A `reference-validation` workflow against Elmer remains future work and
  must not enter ordinary CI.
* **Geometry-precision floor** (spec §15) — refinement is not yet clamped at
  the imported data's own fidelity; the sizing-field floor (`min_size_m`)
  exists but is not derived from source precision metadata.
* **Mesh/error-indicator overlays and convergence plots in the viewer**
  (spec §47–49) — the result panel shows the quality state, per-generation
  table and per-quantity verdicts; field-level overlays of the adaptive mesh
  are not rendered.

## Recommended next numerical improvement

An AMG preconditioner (hypre via PETSc, or pyamg as a pure-Python interim)
— it converts the iterative backend from a validated fallback into the
scalable path every measurement here says is needed above a few million
DOFs, and it is the only item above that unblocks a target rather than
polishing one.

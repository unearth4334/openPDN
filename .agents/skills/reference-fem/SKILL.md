---
name: reference-fem
description: The Reference accuracy tier — quadratic elements, goal-oriented adaptive refinement, error estimation, scalable solving, and what a Reference result is allowed to claim. Read before touching P2, adaptivity, error estimators or Reference job code.
---

# Reference-grade FEM

## What Reference means

The other profiles answer *"give me a sufficiently accurate engineering
solution."* Reference answers *"keep solving until there is evidence the
quantity I care about has converged — or tell me plainly why it hasn't."*

**Reference is not a bigger mesh.** If a change makes Reference into
"Verification with more divisions," the change is wrong. The tier is defined
by the loop and the evidence it produces, not by element count:

```
geometry → mesh → solve → estimate error → mark → re-mesh → solve → ...
        → converged, or a stated limit
```

Governing ADRs: [0012](../../../docs/decisions/0012-quadratic-elements.md)
(P2), [0013](../../../docs/decisions/0013-adaptive-refinement.md) (adaptivity),
[0014](../../../docs/decisions/0014-scalable-linear-solver.md) (solver),
[0015](../../../docs/decisions/0015-reference-job-semantics.md) (job semantics).
Everything in `solver-development/SKILL.md` still applies — this is an
extension of that pipeline, not a parallel one.

## Element order

* P2 is the six-node quadratic triangle, straight-sided, on the affine map.
  Stiffness uses the **degree-2 three-point rule, which is exact** for it — the
  integrand is quadratic on a constant Jacobian with per-element constant
  `sigma t`. Do not "improve" it with a higher-order rule; there is nothing to
  gain and it hides mistakes.
* **Midpoint nodes are appended after the vertex block, never interleaved.**
  The P1 node set is exactly the P2 vertex prefix, which is what lets pad
  containment, via discs and region ranges keep working unindexed.
* A midpoint inside a terminal or via contact region collapses into it — **and
  so does a midpoint whose two endpoint vertices both collapsed there.** Skip
  the second rule and a quadratic edge with pinned ends still bows off the
  pinned value, so the "equipotential region" is only equipotential at nodes.
  That silently reintroduces the mesh-dependent spreading resistance ADR-0010
  built contact regions to avoid.
* P1 is never removed. It is the default for other profiles, the
  cross-validation baseline, and the debugging path.
* **Do not assume P2 is simply "better".** Measured at matched DOF counts:
  ~100x more accurate than P1 on a smooth square, but only ~1.6x on the
  straight-trace board, where the error is set by the terminal-boundary
  discretisation rather than by interior approximation. Raising the order
  does not fix a geometry-limited error -- refining near the feature does.
  That measurement is the case for adaptivity, and it is why "switch
  Reference to P2 and stop" is not the milestone.
* P2 makes `J` linear per element. Percentile statistics must sample the real
  quadratic gradient, not one value per triangle.

## Adaptivity

* Refinement **re-meshes from an error-driven sizing field**. It does not
  subdivide triangles: `mesh.py` has no edge adjacency, no parent triangle and
  no hanging-node support, and is not a constrained triangulation. Local
  subdivision here means writing a second mesher — if that ever becomes the
  right call, it needs its own ADR.
* Consequence to keep in mind: **successive meshes are not nested.** The QoI
  sequence is noisier than a nested hierarchy's, which is exactly why
  extrapolation must verify its assumptions instead of assuming them.
* The estimator is the **edge flux jump**, `eta_K^2 = (1/2) sum_e h_e ||[sigma
  t grad(V) . n]||^2_e`. The interior residual term vanishes identically for
  this problem — zero source, per-element constant `sigma t` — so the jump is
  the whole estimator.
* **Do not switch to recovered-gradient (ZZ) smoothing as the driver.** Patch
  recovery averages flux across a node's elements; where `sigma t` genuinely
  jumps, the true flux is discontinuous, so recovery reports large error where
  the answer is right and would refine every region boundary on the board.
  Fine as a secondary diagnostic, never for marking.
* Goal orientation weights indicators by an **adjoint solution**. The matrix is
  symmetric and the factorisation is already reused across excitations, so the
  adjoint is a second RHS against the same factorisation.
* **But know when it is a no-op.** For a resistance study read at the same
  terminals that drive the excitation, the problem is self-adjoint and the
  dual is exactly `-1` times the primal (measured: ratio -1.000000, standard
  deviation 0.0 across 737 nodes). Dual weighting then reduces to squaring
  the indicator -- it sharpens the marking order and redistributes nothing.
  It earns its extra solve only when the functional and the excitation
  differ: one load's voltage among several, or a probe across terminals
  other than the driven pair. Off by default for that reason.
* Marking is **Dörfler bulk marking**; `theta = 0.7`, calibrated (0.7
  reaches the 1e-3 target in 7 passes vs 0.5's 10, near-equal final DOFs).
  If you change it, re-measure and record.
* **`dQoI` collapse is not convergence evidence on its own.** A mesher
  defect once froze refinement (boundary sampling ignored field demands
  finer than pilot spacing) and successive identical meshes drove `dQoI`
  to 1e-8 while true error sat at 1.1e-3. If `dQoI` plummets while DOFs
  stop growing and `eta` stops falling, suspect stalled refinement before
  celebrating.
* Reference sub-tiers (`ReferenceTier.LOW/MEDIUM/HIGH`) resolve to policy
  numbers at draft time and are forgotten -- never store or hash the tier
  name (ADR-0011's rule for profile names applies).
* **Feature floors override the estimator.** Terminals, via contacts and
  annuli, neck-downs and narrow copper hold a mandatory minimum resolution
  whatever their indicator says. An estimate must never be allowed to conclude
  that an electrically critical feature deserves one triangle.
* A **geometry-precision floor** stops refinement below the imported data's own
  fidelity. Refining past it manufactures detail the source never contained;
  say so in the result instead.

## What converges and what does not

* Converge on **engineering quantities**: `R` for resistance studies; worst
  load voltage, source-to-load drop and total loss for IR drop.
* **Never converge on raw `max|J|`.** At a reentrant corner or an ideal
  terminal edge the continuum solution is singular — the sampled peak rises
  without bound under refinement, so a run gated on it never terminates.
  Report it, label it mesh-sensitive, and converge on `J99`/`J99.9` and
  area-weighted statistics.
* Never refine on `|grad V|` alone either. A high-gradient region may already
  be resolved perfectly well; refinement targets *estimated error*, not
  electrical activity.
* **Stopping is a conjunction**: QoI change *and* estimated error *and* linear
  solve validity *and* current balance *and* energy balance. One metric alone
  is not a stopping rule. This is not theoretical: an implementation that
  stopped on QoI change plus conservation alone declared convergence on two
  non-nested meshes that happened to agree. Requiring the estimator to have
  *itself stabilised* (stopped moving pass over pass), plus a confirmation
  count, is what tells re-meshing noise apart from convergence — asking it to
  *halve from the coarsest pass* instead is unsatisfiable near a genuine
  singular contribution (measured on a 392-via board: plateaued at ~55% of
  its starting value for six passes straight while the QoI had already
  settled to 1e-11), and stalled real convergence for no numerical reason.
* **Conservation gates convergence at the *error* threshold, not the warning
  one.** ADR-0010 §6 defines 1e-6 as "worth a diagnostic" and 1e-3 as "a real
  failure" — an earlier `AdaptivePolicy` copied the warning figure as
  `max_power_mismatch`'s hard pass/fail gate, so any board whose (still
  perfectly healthy, still merely-flagged) power balance sat between the two
  could never converge no matter how many passes it ran. Measured on the
  same 392-via board: power mismatch settled at 1.0e-5 to 1.4e-5, an order
  past the old gate but two orders inside the error threshold, and did not
  trend toward zero under further refinement (a lumped via-barrel accounting
  characteristic of a via-dense board, not something more mesh buys back).
  Current imbalance measured 3e-8 to 4e-8 on the same board — nowhere near
  either threshold — so only the power-mismatch default moved; don't loosen
  a gate that measurement doesn't say is the blocker.
* **Know when adaptivity is the wrong tool.** Measured: it wins big on a
  plane-neck-plane board (error in the neck, copper in the planes) and does
  *not* beat uniform refinement on a series-widths board, where total
  resistance is spread along the whole path. Adaptivity pays when error and
  copper are in different places. Reaching for it on a board where they are
  not is wasted compute, not a bug to chase.
* **Check your reference before trusting any error figure.** A reference
  taken from the finest *uniform P1* mesh was 0.5 % off and made every error
  measured against it wrong. P1 climbs towards the limit while P2 settles
  onto it -- when the two orders disagree by more than their own apparent
  convergence, suspect the reference first. Measured: adaptive P2 reaches
  1.1e-3 at 1,388 DOFs where uniform P1 needs 143,213 for 2.0e-3.
* **Re-meshing noise is the floor on any convergence claim.** Successive
  meshes are non-nested, and re-meshing alone moves terminal resistance by a
  few parts per thousand. Do not assert monotone error decrease that the
  mesher cannot deliver; assert bands, and verify monotonicity before
  extrapolating anything.

## Linear solving

* The reduced system is **SPD** — because Dirichlet BCs are applied by
  row/column elimination, which preserves symmetry. CG applies only as long as
  that stays true. If anyone changes to a penalty or Lagrange-multiplier
  scheme, this assumption dies with it.
* Iterative tolerance is **derived from the run's target FEM error**, at most
  `0.05` of it — the linear solve must never be the accuracy-limiting step.
* **Max-iterations reached is a failure, not a result.** Report
  `NUMERICAL_FAILURE`.
* The residual is not accuracy. It bounds how well the *discrete system* was
  solved, never how well that system approximates the physics. An iterative
  backend makes this easier to get wrong, so it is worth restating.
* Direct-vs-iterative cross-validation on the analytical suite is a release
  gate, not an optional extra. Measured: the two agree to 1e-14 on terminal
  resistance across 287-35,976 DOFs.
* **The iterative backend is scalable now, via pyamg smoothed-aggregation
  AMG** (PETSc+hypre remains unbuildable here; swapping to it later is a
  preconditioner change behind the same report fields). Measured: AMG
  iterations are near mesh-independent (13 -> 42 over 36k -> 2.28M DOFs),
  and the 2.28M-DOF system Jacobi-CG could not solve at all runs in 12.9 s
  / 3.16 GiB against direct's 256.5 s / 11.25 GiB. `AUTO` crosses to
  iterative at 200k DOFs -- **only when AMG is available**: with the Jacobi
  fallback (iterations grow as sqrt(DOFs); 112 -> 940 over 287 -> 36k;
  outright non-convergence at 2.24M) `AUTO` means direct, because routing
  to a backend that predictably cannot converge turns feasible jobs into
  guaranteed failures. Do not change either behaviour without re-measuring
  both curves.
* The reason iterative exists is the direct factorisation's fill-in: 2.6x
  the matrix non-zeros at 287 DOFs, 22.5x at 143,213 (189 -> 1,880 bytes per
  DOF, still climbing). Iterative memory is flat.

## What a Reference result may claim

* Four error sources stay **separate and never summed**: geometry
  representation, FEM discretisation, linear algebra, and physical/model-form
  uncertainty. A converged mesh says nothing about assumed via plating.
* When discretisation error drops below input uncertainty, **say so**. The
  numerical answer being more precise than the model is information, not an
  embarrassment to smooth over.
* Convergence is **per quantity**. A singular-tagged quantity failing to
  converge does not by itself make the run `NOT_CONVERGED`.
* Extrapolation is published **only** after checking monotonicity, asymptotic
  regime and observed order. Otherwise report the finest-mesh value alone.
* Quality states are `CONVERGED`, `CONVERGED_WITH_MODEL_LIMITATIONS`,
  `RESOURCE_LIMITED`, `NOT_CONVERGED`, `NUMERICAL_FAILURE`. They are properties
  of the *result*; the job lifecycle is unchanged and
  `COMPLETED_WITH_WARNINGS` already carries the non-clean finishes.
* **A run that hit its ceiling while still moving is `RESOURCE_LIMITED`**, and
  the UI shows that, not a green tick. Hiding an unconverged result behind
  "Complete" is the single worst failure mode of this tier.

## Contacts

* Terminals are equipotential **pad regions** and vias couple through an
  equipotential **contact disc** at the barrel's outer radius. This is not a
  nicety: measured on a centre-fed sheet, a distributed contact converges to
  0.4272 mOhm while a single-node contact adds a constant +0.0587 mOhm per
  halving of the element size forever -- matching `ln(2)/(2 pi Gs)` from the
  2-D spreading formula to 8 %.
* A point contact therefore has **no continuum limit**, and refining the mesh
  makes it worse. `numerics.point_source_singularity` says exactly that; do
  not treat it as "slightly less accurate".

## Jobs

* A Reference spec freezes a **policy** (targets, ceilings, estimator
  parameters, element order, solver policy) resolved to absolute numbers — not
  a mesh, because the mesh is the run's output. The signature hashes that
  policy; the achieved mesh goes in the manifest.
* This works only because **the loop is deterministic.** There is no RNG in
  `solver/fem` and Qhull is deterministic per point set — keep it that way:
  deterministic tie-breaks in marking, no order-dependent iteration over
  unordered containers.
* Guardrails are enforced **server-side** against administrative ceilings,
  whatever the client asked for. Over-budget work is refused, never degraded.
* Reference jobs are a separate priority class and admission accounts for
  estimated *memory* — one Reference job can take a whole worker.
* Completed generations are checkpointed at every pass boundary, and a
  requeued run resumes **bit-for-bit** -- the checkpoint stores only
  generation metrics, sizing-field seeds and the streak, because re-meshing
  is deterministic and everything else re-derives. Checkpoints live outside
  `results/` so the stale-working sweep cannot destroy them; a signature
  mismatch discards rather than trusts. Cancellation publishes the partial
  as `NOT_CONVERGED` with the job CANCELLED -- kept *and* labelled. Never
  present an intermediate generation as Reference quality.
* **An adaptive spec must reach the adaptive path.** The worker branches on
  `spec.reference_policy`; a Reference job that fell through to the
  fixed-mesh branch would silently ignore its whole policy and still publish
  under a Reference label. That defect existed and was invisible because
  nothing downstream checked -- if you add a code path here, check it.
* Publish the **final generation's** field data. Re-solving to recover it
  lands on the initial mesh and ships fields that disagree with the result
  beside them.

## Wiring, and how it fails silently

The tier has three seams where a piece can exist, pass its own tests, and
still never run. All three have already broken once:

* **`AccuracyProfile` -> `_SHAPES` in `accuracy.py`.** Adding `REFERENCE`
  without a shape made `SimulationService.plan` raise `KeyError` for every
  Reference job -- the whole tier unreachable, while the adaptive loop,
  worker branch and job semantics all passed their own tests. There is now
  an import-time check that every profile resolves, and
  `tests/integration/test_simulation_service.py` parameterises over the enum.
* **`spec.reference_policy` -> the worker branch.** Before that branch
  existed the worker ran Reference jobs as plain fixed-mesh solves, ignoring
  the policy and publishing under a Reference label.
* **The API `Literal`.** `reference` has to be in the accuracy union and the
  policy has to be carried through `to_draft`, or the tier is invisible to
  every client.

The pattern: **test the path a real request takes**, not only the pieces.
Parameterise over the enum wherever profiles are handled, so adding one
without wiring it fails immediately instead of at the first user request.

## Validating this tier

Measurement, not assertion. Specifically:

* **Method of manufactured solutions** for the basis functions: pick a smooth
  `V(x,y)`, derive the forcing and BCs, and check the **observed convergence
  order**, not merely that error shrank. A wrong basis function still
  converges — just at the wrong rate.
* **P1-vs-P2 error per DOF** on `straight_trace_board`, where `R = L/(sigma w
  t)` is exact.
* **Adaptive-vs-uniform**: a real measured table showing adaptivity reaching a
  given resistance error at substantially fewer DOFs. If it doesn't, the
  estimator or the marking is wrong and that is the finding.
* Conservation checks from ADR-0010 must still pass under P2 and under every
  adaptive generation.

Report failures and inconclusive results explicitly. A validation table that
only ever shows improvement is a sign the tests are not testing.

# ADR-0013: Goal-oriented adaptive refinement for the Reference tier

## Status

Accepted. Extends ADR-0010 (meshing) and is the numerical core of the
Reference tier defined with ADR-0012, ADR-0014 and ADR-0015.

## Context

Every existing accuracy profile refines *globally*: a denser sizing field
everywhere, whether or not the extra elements reduce the error in the answer
the engineer actually reads. On a PCB most of the copper is a quiet plane
interior where the field is nearly uniform, while the error concentrates in a
small fraction of the area — neck-downs, via contacts, pad edges, reentrant
corners. Uniform refinement spends almost all of its DOFs where they change
nothing.

The mesher constrains the available strategies. `mesh.py` is a one-shot
graded, filtered Delaunay triangulation: it has no edge adjacency, no parent
triangle, no hanging-node support, and it is not a *constrained* triangulation
(boundary conformity comes from dense sampling plus containment filtering, per
ADR-0010 §2). Classic local refinement — bisect or red-green-subdivide the
marked triangles and keep the rest of the mesh — has no scaffolding here and
would amount to writing a second mesher.

What `mesh.py` does have is a genuine spatially varying *sizing field*: target
element size is already computed per point from ray-cast conductor width and
clearance, then grown outward at a bounded rate. That is exactly the interface
an error estimator can drive.

## Decision

1. **Refinement is by re-meshing from an error-driven sizing field, not by
   local subdivision.** Each pass computes a per-element error indicator,
   marks elements, converts the marks into a reduced target size in those
   elements' neighbourhoods, and re-runs the existing mesher over the whole
   region with the updated field. This reuses the graded-sizing and
   growth-limiting machinery instead of duplicating it, and keeps every mesh a
   plain conforming Delaunay mesh with no hanging nodes.

2. **The estimator is the residual-based edge flux jump.**

   ```
   eta_K^2  =  (1/2) * sum over edges e of K  of  h_e * || [ sigma t grad(V) . n ] ||^2_e
   ```

   The interior residual term of the standard explicit estimator vanishes
   identically here: the source term is zero and, per element, `sigma t` is
   constant, so `div(sigma t grad V)` is zero for P1 and is integrated exactly
   for P2. What remains is the jump in normal sheet current across element
   edges — which is precisely the physical continuity condition the
   discretisation violates, and it remains the right quantity at region and
   layer boundaries where `sigma t` genuinely jumps.

3. **Recovered-gradient (Zienkiewicz–Zhu style) smoothing is rejected as the
   primary estimator.** Patch recovery averages the flux across a node's
   surrounding elements. At a sheet-conductance discontinuity the true flux
   *is* discontinuous, so recovery reports a large error where the solution is
   in fact correct, and would drive refinement into every region boundary on
   the board regardless of accuracy. It may be used as a secondary diagnostic;
   it must not drive marking.

4. **Goal orientation is by dual weighting, exploiting the symmetric matrix.**
   Refining on `eta_K` alone minimises energy-norm error, which is not what the
   user asked for; the engineering answer is a functional of the solution.
   Reference weights the element indicators by an adjoint (dual) solution for
   the quantity of interest. The conductance matrix is symmetric, and ADR-0010
   already established that the factorisation is reused across excitations —
   so the adjoint solve is a second right-hand side against the *same*
   factorisation and is close to free. This is the reason a
   dual-weighted-residual scheme is affordable here at all, and it is the
   decisive argument for it over plain energy-norm refinement.

5. **Quantities of interest, per study kind:**
   * resistance studies — `R` between the probe terminals;
   * IR-drop studies — worst load-terminal voltage, source-to-load drop, and
     total resistive loss.

   Convergence is judged on these. Raw `max|J|` is **not** a convergence
   criterion: at a reentrant corner or an ideal terminal edge the continuum
   solution is genuinely singular, so the sampled peak rises without bound
   under refinement and would prevent any solve from ever converging. Peak
   current density is reported, labelled mesh-sensitive, and accompanied by
   `J99`/`J99.9` and area-weighted statistics, which do converge.

6. **Marking is Dörfler bulk marking**: refine the smallest set `M` with

   ```
   sum over M of eta_K^2  >=  theta * sum over all K of eta_K^2
   ```

   Default `theta = 0.5`, configurable. This value is a starting point taken
   from the usual `0.4`–`0.7` range, **not** a measured optimum for this
   problem class; Phase 2 must calibrate it against real boards and record
   what it measured.

7. **Geometry and feature floors override the estimator.** Terminal
   boundaries, via contacts and annular rings, neck-downs and narrow copper
   keep a mandatory minimum resolution regardless of how small their indicator
   is. An estimator is an estimate; it must never be allowed to conclude that
   an electrically critical feature deserves one triangle. Symmetrically, a
   hard floor on element size derived from the imported geometry's own
   precision stops refinement from manufacturing detail the source data never
   contained.

8. **Stopping is a conjunction, never a single metric.** A pass ends the loop
   only when the QoI relative change is within target *and* the estimated
   discretisation error is within target *and* the linear solve is valid *and*
   current imbalance is within tolerance *and* energy balance is within
   tolerance. Any guardrail hit first (max passes, max DOFs, max memory, max
   wall-clock, size floor) terminates the loop with a resource-limited result
   rather than a converged one — see ADR-0015 for the resulting states.

9. **Determinism is required.** The mesher contains no randomised algorithm
   today (no RNG anywhere in `solver/fem`), and Qhull is deterministic for a
   given point set, so identical inputs already produce identical meshes. The
   adaptive loop must preserve that: marking ties broken deterministically,
   no iteration over unordered containers where order affects the field.

## Consequences

* **Successive meshes are not nested.** Re-meshing from a new field is not a
  refinement of the previous triangulation, so the QoI sequence is noisier
  than a nested hierarchy would give, and Richardson extrapolation — which
  assumes a clean asymptotic sequence — must verify monotonicity and estimate
  the observed order before it is applied, never be applied by default
  (ADR-0015).
* Each pass re-meshes and re-factorises from scratch. No solution transfer or
  prolongation is needed, which removes a whole class of interpolation error,
  at the cost of discarding the previous factorisation.
* The edge adjacency structure ADR-0012 adds for P2 midpoint nodes is the same
  structure the flux-jump estimator needs. Built once, used by both.
* The estimator is an *indicator*, not a certified error bound. Reported
  values are labelled as estimates; the honest convergence evidence remains
  the observed QoI change across passes.
* Adaptivity's benefit must be demonstrated, not asserted: Phase 2's
  acceptance is a measured table showing adaptive refinement reaching a given
  terminal-resistance error at substantially fewer DOFs than uniform
  refinement on the same board.

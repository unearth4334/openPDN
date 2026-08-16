# ADR-0012: Quadratic (P2) elements for the Reference accuracy tier

## Status

Accepted. Extends ADR-0010, whose P1 formulation remains the default for
every non-Reference profile.

## Context

The Preview/Standard/High/Verification ladder is a set of fixed mesh-density
presets over P1 (linear triangle) elements. Reaching substantially lower
discretisation error by shrinking P1 elements alone is inefficient: P1 gives
`O(h)` convergence in the energy norm, so each halving of error costs roughly
four times the degrees of freedom in 2-D. Quadratic elements give `O(h^2)`
on smooth solutions, which buys the same accuracy at far fewer DOFs.

Adding P2 here is not a library flag. `problem.py` assembles the P1 stiffness
from the closed-form `K_e = Gs/(4A) (b b^T + c c^T)`; there is no
shape-function abstraction and no quadrature machinery at all, because P1
gradients are constant per element. `mesh.py` produces triangle *vertices*
only, with no edge list and no edge-to-triangle adjacency. P2 therefore needs
new basis functions, a quadrature rule, an edge enumeration and a second
class of node.

Two properties of the existing code make this tractable and must not be lost:
terminal contact regions and via contact discs are equipotential *regions*
collapsed by union-find over mesh nodes (ADR-0010 §3, §4), and every solve
reports conservation (ADR-0010 §6). Both must continue to hold exactly under
P2, and neither is automatic.

## Decision

1. **Element**: the six-node quadratic triangle — three vertex nodes and three
   edge-midpoint nodes, straight-sided, on the same affine map the P1 path
   already uses. No curved (isoparametric) edges: the mesher approximates
   copper boundaries by dense sampling (ADR-0010 §2), so curved elements would
   add cost against a boundary that is already a polygon.

2. **Quadrature**: the symmetric three-point rule of degree 2 (barycentric
   permutations of `(2/3, 1/6, 1/6)`, equal weights). On a straight-sided
   triangle the Jacobian is constant and P2 gradients are linear, so the
   stiffness integrand `grad(phi_i) . grad(phi_j)` is exactly quadratic and
   this rule integrates it *exactly*, not approximately. Sheet conductance
   `sigma t` is constant per element and does not raise the degree.

3. **Node numbering is a prefix, not an interleave.** Vertex nodes keep the
   indices `mesh.py` already assigns, `[0, n_vertices)`; edge-midpoint nodes
   are appended as `[n_vertices, n_vertices + n_edges)`. The P1 node set is
   therefore exactly the P2 vertex block. Every existing vertex-indexed
   routine — pad-containment collapsing, via contact discs, region ranges,
   component labelling — keeps working on that prefix without reindexing.

4. **Equipotential regions must be equipotential everywhere on the edge, not
   just at nodes.** A midpoint node collapses into a terminal or via contact
   region when it passes the same containment test used for vertices, *and*
   unconditionally when both of its endpoint vertices collapsed into the same
   region. The second clause is not redundant: without it a quadratic edge
   with both ends pinned can still bow away from the pinned potential, so a
   region that is equipotential at its vertices would not be an equipotential
   region. That would quietly break the contact-region model ADR-0010 chose
   precisely to avoid mesh-dependent spreading resistance.

5. **Element order is a study input, not a solver identity.** `MeshSettings`
   gains an element-order field; the `fem-2p5d` solver serves both orders
   rather than a second solver being registered. `SolverCapabilities` gains a
   supported-orders field so a backend that cannot do P2 declares it and is
   refused before dispatch, per ADR-0003 rule 4.

6. **P1 is retained permanently** as the default for existing profiles, as the
   cross-validation baseline for P2, and as the debugging path. Any P2 result
   must be reproducible against a P1 result on the same geometry to within
   discretisation error; a P2 path that cannot be cross-checked against P1 is
   not trusted.

7. **Current density is per-element linear under P2**, not constant. Field
   reconstruction and the `J99`/percentile statistics must sample the
   quadratic solution's actual gradient rather than assume one value per
   triangle.

## Consequences

* DOF count rises roughly fourfold for a fixed triangulation: for a large
  planar triangulation `E ~= 3V`, so P2 DOFs `= V + E ~= 4V`. Matrix non-zeros
  and factorisation cost rise faster still. `BYTES_PER_DOF` and the
  compute-class thresholds in `fem_planner.py` are calibrated for P1 and must
  be re-measured for P2 rather than reused.
* The DOF budget (`worker_max_dofs`) is an absolute count and needs no change
  in meaning, but the same board at the same mesh density now costs ~4x
  against it. Estimation must account for element order or it will
  under-report by that factor.
* `mesh.py` gains an edge enumeration and edge-to-triangle adjacency it does
  not have today. That structure is also what an error estimator needs for
  edge flux jumps (ADR-0013), so it is built once and used twice.
* P2 is validated by the method of manufactured solutions and by observed
  convergence *order*, not merely by "the error got smaller" — a wrong basis
  function can still converge, just at the wrong rate. Straight-trace
  resistance additionally gives a P1-vs-P2 error-per-DOF comparison on real
  board geometry.
* Known limit, stated rather than hidden: `O(h^2)` is the smooth-solution
  rate. PCB geometry has reentrant copper corners, sheet-conductance jumps at
  region and layer boundaries, and ideal terminal-edge transitions, all of
  which are singular or non-smooth and will depress the observed rate locally.
  P2 buys less at a reentrant corner than it does in open copper, which is
  part of the argument for adaptivity (ADR-0013) rather than uniform P2.

## Measured (implementation, 2026-08-16)

The above was written before implementation. What the validation suite
actually measured, and it sharpens the last point considerably:

* **Basis correctness**, on a square with harmonic exact solutions: observed
  convergence order P1 `1.88`, P2 `3.89`–`3.91` in the nodal norm, and P2
  reproduces a harmonic quadratic to `1.3e-16` where P1 gives `9.1e-4`.
* **A uniform grid is not a neutral test bed.** On it the P1 stiffness
  coincides with the five-point finite-difference Laplacian, whose truncation
  error involves only fourth derivatives, so P1 is *nodally exact* for
  harmonic quadratics too and the P1/P2 distinction vanishes for reasons that
  have nothing to do with the basis. The element tests jitter the mesh
  because of this.
* **The per-DOF gain is problem-dependent, and the spread is large**: at
  matched DOF counts, P2 is about `100x` more accurate than P1 on the smooth
  square, but only about `1.6x` on the straight-trace board (`6072` vs `6071`
  DOFs, relative error `6.8e-4` vs `4.3e-4`).

  The gap is the finding. On that board the dominant error is the
  discretisation of the equipotential *terminal boundary*, which improves
  with smaller elements near the pads and not with polynomial order -- P1 and
  P2 converge at the same depressed rate there. This is measured evidence for
  ADR-0013's adaptive refinement over simply raising the order everywhere,
  and it is why P1 is retained rather than superseded.
* **§4's containment clause is load-bearing, not belt-and-braces.** It was
  deferred during a first implementation pass, and P2 came out *worse* than
  P1 (`9.0e-3` against `6.8e-3` at `h = 0.5 mm`): with pad-straddling
  midpoints left free, the equipotential region stopped short of the real
  copper contact and lengthened the conduction path. Implementing the clause
  moved P2 to `1.4e-3`. Both clauses are required.

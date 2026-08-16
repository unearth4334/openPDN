# ADR-0010: In-house 2.5-D sheet-conduction FEM with a direct sparse solve

## Status

Accepted. Extended by ADR-0012 (quadratic elements as an additional element
order for the Reference tier; P1 below remains the default), ADR-0013
(estimator-driven adaptive re-meshing over the sizing field decided in §2) and
ADR-0014 (an optional iterative backend beside the direct solve of §5, which
§5 already anticipated). None of them supersede the formulation, the meshing
strategy, the terminal and via treatment, or the conservation rules below.

## Context

The first physical solver must compute DC voltage, current density, via
current, terminal-to-terminal resistance and resistive loss on imported
boards, with accuracy and auditability ahead of speed. Reference
implementations were inspected before building:

* **padne** (2.5-D FEM, KiCad-focused) validates the formulation choice, but
  is GPL-3.0 with a CGAL/GMP/Boost meshing stack, so neither its code nor its
  mesher can be adopted by an Apache-2.0 project.
* **Shewchuk's Triangle** is licensed for private/research/institutional use
  only; commercial use requires arrangement with the author. Unusable as a
  hard dependency.
* **Gmsh** is GPL-2+. Same conclusion.
* **SciPy** (Qhull Delaunay, SuperLU) and **NumPy** are permissively licensed
  and already trusted infrastructure.

## Decision

1. **Formulation**: 2.5-D sheet conduction, `div(sigma t grad V) = 0` per
   copper layer, P1 (linear triangle) elements, double precision everywhere.
2. **Meshing** is in-house: a graded, filtered Delaunay triangulation
   (`openpdn.solver.fem.mesh`). Boundary points are placed at a spacing
   graded by ray-cast local conductor *width* (k elements across a feature)
   and *clearance* (so Delaunay edges cannot bridge slots); interiors are
   hexagonal lattices selected by a growth-limited sizing field; triangles
   are kept only if their centroid and five samples of every edge lie inside
   the copper. Coverage ratio and angle statistics are reported on every
   mesh. This trades guaranteed boundary conformity (which a constrained
   mesher would give) for a license-clean, deterministic, fully auditable
   algorithm whose error is *measured* -- by coverage stats and by the
   validation suite's convergence tests -- rather than assumed.
3. **Terminals** are equipotential contact regions: every mesh vertex inside
   a terminal's pad copper collapses to one degree of freedom, across layers.
   A pad without an outline degrades to its nearest vertex and the result
   carries `numerics.point_source_singularity`.
4. **Vias** are lumped conductances with the exact annulus
   `pi[(r+t)^2 - r^2]` (never thin-wall), one segment per consecutive pair of
   connected conductive layers, barrel lengths from stackup midplane
   z-distances. Sheet coupling is an equipotential **contact disc** at the
   barrel's outer radius -- a single-node coupling would accrue a
   logarithmically mesh-dependent spreading resistance.
5. **Linear solve**: SciPy SuperLU direct factorisation of the reduced
   Dirichlet system. Deterministic, no iterative tuning, factorisation
   reusable across excitations. Iterative/PETSc/Elmer backends remain
   possible behind the same `ElectricalSolver` contract.
6. **Conservation is checked, not assumed**: every solve reports current
   imbalance and terminal-vs-integrated power mismatch; beyond 1e-6 the
   result carries a warning, beyond 1e-3 an error diagnostic.
7. **Layering**: solver adapters may import the geometry *contract*
   (`openpdn.geometry.api`) -- normalised copper is exactly what that
   contract exists to describe -- but never the concrete Shapely engine.
   `LAYER_RULES` was extended accordingly.

## Consequences

* NumPy and SciPy move from the `solver` extra to runtime dependencies.
* `fem-2p5d` appears in `VALIDATED_SOLVERS` backed by
  `tests/validation/test_fem_validation.py`: straight-trace resistance
  converging below 0.1 %, exact via barrels, four-layer stacks, linearity,
  conservation and disconnected-copper refusal.
* Known limits, stated rather than hidden: no constrained triangulation
  (boundary fidelity is sampling-based), current crowding inside barrels and
  vertical near-pad fields are outside the 2.5-D model, and triangle quality
  is reported but not guaranteed by construction.

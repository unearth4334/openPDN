---
name: solver-development
description: The physics openPDN solves, the 2.5-D formulation, the pipeline stages and their caching, and the numerical honesty rules. Read before writing solver, meshing or result code.
---

# Solver development

## The physics

Steady-state current conduction. No time dependence, no displacement current:

$$\nabla \cdot (\sigma \nabla V) = 0$$

with current density

$$\vec{J} = -\sigma \nabla V$$

and resistive loss density $p = \vec{J}\cdot\vec{E} = \sigma|\nabla V|^2$.

Boundary conditions:

* **Dirichlet** at a voltage source: $V = V_0$ on the terminal.
* **Neumann** at a current load: prescribed normal current density, or a
  prescribed total current over the terminal's footprint.
* **Homogeneous Neumann** everywhere else: no current leaves copper into air.

A well-posed problem needs at least one Dirichlet condition — `AnalysisStudy`
rejects a study without a source for exactly this reason.

## The 2.5-D formulation

PCB copper is thin: 35 µm against millimetres of lateral extent. Integrating
through the thickness reduces each layer to a sheet problem,

$$\nabla_{xy} \cdot (\sigma t \, \nabla_{xy} V) = 0$$

where $\sigma t$ is the sheet conductance and $1/(\sigma t)$ the familiar
sheet resistance in ohms per square (≈ 0.5 mΩ/□ for 1 oz copper).

Layers couple through vias. In the preferred fast model each via is a **lumped
conductance** between the two sheet domains it connects:

$$G_\text{via} = \frac{\sigma A_\text{barrel}}{L}, \qquad
A_\text{barrel} = \pi\left[(r_\text{hole}+t_\text{plate})^2 - r_\text{hole}^2\right]$$

This is the preferred first solver: it captures IR drop, current sharing
between layers and via current within a few percent for typical geometries, at
a fraction of the cost of a volumetric mesh. `ElmerFEM` is planned behind the
same contract for cases where the thin-sheet assumption fails — thick copper,
heavy plating, electrothermal coupling.

State the assumption in results. A 2.5-D run reports
`ResultFidelity.SHEET_2P5D`, and its diagnostics say where the model is thin:
current crowding through a via barrel, in-plane fields near a pad, and any
region where thickness is not small against lateral extent.

## Pipeline stages

Keep these separate. They have different inputs, different costs and different
cache keys.

```text
Board + Study
    │
    ├─ 1. geometry normalisation   copper grouped by (net, layer), unions, clearances
    ├─ 2. meshing                  triangulation per sheet domain
    ├─ 3. assembly                 stiffness matrix from σ·t and via conductances
    ├─ 4. boundary conditions      source potentials, load currents  ← cheap, changes often
    ├─ 5. solve                    linear system
    └─ 6. post-processing          J, IR drop, via current, loss density, probes
```

Rules:

* **Meshing is not solving.** A mesh depends on geometry, materials and mesh
  settings — never on the value of a source or a load.
* **Assembly is not boundary-condition application.** The stiffness matrix
  depends on conductance; excitations enter the right-hand side.
* **Changing a load current must not re-import or re-mesh.** That is what
  `StagedElectricalSolver.prepare()` and `PreparedProblem.solve_with()` exist
  for. If editing "4.0 A" to "4.2 A" triggers a re-import, the caching is wrong.
* **Cache each stage separately**, keyed by a hash of its actual inputs.
  Geometry normalisation and mesh caches key on board geometry + materials +
  mesh settings. Report hits and misses via `SolverRunStats.cache_hit` and the
  `cache.hit` / `cache.miss` events.

## Numerical honesty

**Convergence is a result, not a detail.** Report iterations, residual and
`converged` in `SolverRunStats`. A non-converged solve raises
`SolverConvergenceError` carrying the achieved residual — never returns numbers
with a shrug.

**Point sources are singular.** In a continuum, current density diverges
logarithmically at a point terminal; the discrete value simply tracks mesh
refinement. Never present that as a physical hot spot, never let it set a
colour-map maximum, and never let it fail a current-density check. Apply
terminals over their real pad footprint where geometry allows, and emit a
diagnostic (`numerics.point_source_singularity`) where a terminal had to be
reduced to a point.

**Report the mesh you used.** Nodes and elements go in `SolverRunStats` so a
result can be reproduced and a suspicious answer can be re-run finer.

**Ill-conditioning has causes worth naming.** A floating sub-net with no
Dirichlet path, a zero-conductance region from a missing thickness, or a
degenerate mesh element each produce a singular matrix. Diagnose the cause;
"singular matrix" alone tells the user nothing they can act on.

## Implementing a backend

1. Implement `ElectricalSolver` (and `StagedElectricalSolver` if the stages
   separate).
2. `describe()` returns honest `SolverCapabilities` — including which
   `ViaModel`s are supported. `AnalysisService` refuses a mismatch before you
   are called.
3. Raise `SolverUnsupportedFeatureError` rather than approximating something
   the user did not ask for.
4. Translate every backend exception into a `SolverError` subclass.
5. Emit a `Diagnostic` for every assumption: substituted thickness, ignored
   via, unmodelled feature, mesh quality warning.
6. Return the common result model. No backend-specific result types reach the
   application layer.
7. Register in `build_container`; add validation cases; add the solver to
   `VALIDATED_SOLVERS` only once those cases pass.

## Numerical dependencies

NumPy, SciPy and Shapely are declared in the `solver` extra and are *not*
runtime dependencies yet. The first real solver moves them into `dependencies`.
They remain invisible to the domain and the application layer.

Sparse assembly: build COO triplets and convert to CSR once; do not assemble
into a dense matrix "for now". Prefer a direct sparse factorisation for the
sizes 2.5-D PCB problems produce, and reuse the factorisation across
excitations — that reuse is the entire point of separating stages 3 and 4.

## What not to build yet

Do not start a large general-purpose FEM engine, and do not reimplement
padne/FYPA. The first milestone is the smallest correct 2.5-D path for a single
net on a single layer, validated against `R = L/(σwt)`, then extended to
multiple layers with lumped vias.

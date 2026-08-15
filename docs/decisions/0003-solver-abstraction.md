# ADR-0003: Solvers behind a contract, resolved by registry

## Status

Accepted — 2026-08-14.

## Context

openPDN expects several numerical backends over its life:

* a fast 2.5-D sheet-conduction solver, drawing on concepts from padne/FYPA;
* ElmerFEM for volumetric and electrothermal analysis;
* a mock backend for pipeline testing.

They differ in everything: process model (in-process vs subprocess), input
format, physics supported, mesh control, result detail. If a user-facing
feature depends on any one of them, adding the next one means rewriting
features rather than adding a backend.

There is a second risk specific to this domain. A backend that cannot do what a
study asks could silently do something close instead — solve with lumped vias
when resolved 3-D was requested, or substitute a default thickness. The user
would receive plausible numbers for a different problem.

## Decision

Define the contract in `packages/solver-api` (`openpdn.solver.api`), depending
on the domain only:

```python
class ElectricalSolver(Protocol):
    def describe(self) -> SolverDescriptor: ...
    def solve(self, board: Board, study: AnalysisStudy) -> ElectricalAnalysisResult: ...
```

with an optional staged extension for backends that can separate meshing and
assembly from excitation:

```python
class StagedElectricalSolver(ElectricalSolver, Protocol):
    def prepare(self, board: Board, study: AnalysisStudy) -> PreparedProblem: ...
```

Supporting rules:

1. `SolverDescriptor.capabilities` declares fidelity and which `ViaModel`s are
   supported. `AnalysisService` checks before dispatching and refuses a
   mismatch; a backend must also raise `SolverUnsupportedFeatureError` rather
   than approximate.
2. All backends return the same result model, including a truthful
   `ResultFidelity`. `ResultFidelity.MOCK` means no conduction problem was
   solved and must never be rendered as a simulation.
3. Backend exceptions are translated into `SolverError` subclasses at the
   adapter boundary.
4. Backend-specific configuration lives inside the adapter. `MeshSettings` is
   solver-independent; Elmer `.sif` options and padne parameters never appear
   in the domain or the application layer.
5. Solvers are resolved by name through `SolverRegistry`. The composition root
   is the only module that names a concrete class.
6. `tests/validation/test_solver_validation_gate.py` fails if a solver
   advertises physical fidelity without validation cases against
   `tests/validation/analytical.py`.

The mock backend is not a placeholder for real results: it echoes the study's
own boundary conditions, tagged `MOCK`, with a warning diagnostic.

## Consequences

* Adding a backend is a new package plus one line in the composition root.
* Users can be told *why* a backend is unavailable: descriptors stay listed
  with `available=False` and a reason.
* Callers must handle a refusal — an unsupported study fails loudly instead of
  quietly changing physics. This is the intended cost.
* The staged interface exists before any solver implements it, so the caching
  boundary (geometry/mesh/matrix vs excitation) is designed in rather than
  retrofitted.
* Result-model changes affect every backend, so the model stays small and is
  extended deliberately.

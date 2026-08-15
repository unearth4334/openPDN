# ADR-0001: Clean architecture with an enforced dependency direction

## Status

Accepted — 2026-08-14. The decision stands; the first import format named in
the Context below was later changed by [ADR-0006](0006-ipc2581-reference-import-format.md).

## Context

openPDN sits between several external technologies that will all change: a PCB
interchange format first and others later (at the time of writing this was
expected to be ODB++ via OdbDesign; ADR-0006 later made IPC-2581 the reference
format, which this layering absorbed without a code change); a fast 2.5-D
solver first and ElmerFEM afterwards; FastAPI, React, a container registry and
an orchestrator around the outside.

EDA tooling has a well-known failure mode: the input format's data model
becomes the application's data model. Then the solver reads the format's
structures, the UI reads solver structures, and adding a second format or a
second backend means touching everything. The same happens when a web framework's models
become the engineering model.

We also want the engineering core testable without any of that installed —
including on a machine with no solver, no Node and no Docker.

## Decision

Layer the system, and let dependencies point only inward:

```text
UI / CLI / API  ──►  Application Services  ──►  Domain Model
                            │                       ▲
                            ▼                       │
                     Contracts (ports)  ◄──  Infrastructure Adapters
```

* The **domain** (`packages/domain`) imports the standard library only. No
  ODB++, OdbDesign, padne, FYPA, Elmer, FastAPI, Pydantic, NumPy, SciPy,
  Shapely or SQL. Domain entities are stdlib dataclasses.
* **Contracts** (`openpdn.solver.api`, `openpdn.pcb_import.api`) depend on the
  domain only and contain no implementation.
* **Application services** depend on the domain and the contracts. They never
  import a concrete adapter or a framework, and never read the environment.
* **Adapters** (importers, solvers, infrastructure) may use any library and
  depend inward.
* The **composition root** (`infrastructure/container.py`) is the only module
  that names concrete adapters.
* Surfaces (`apps/api`, `apps/cli`) are thin and share application services.

Enforce it with a test: `tests/unit/test_architecture_boundaries.py` parses
every module — including `if TYPE_CHECKING` imports — and fails the build on a
wrong-direction edge, a third-party import in a pure layer, or a concrete
adapter named outside the composition root.

The monorepo ships as one distribution with several source roots, rather than
several wheels. The boundary is enforced by the test, not by packaging, so
there is no dependency-resolution cost for the guarantee.

## Consequences

* Adding an import format or a solver touches its own package plus one line of
  the composition root. No application, API, CLI or UI change.
* The domain is unit-testable on a bare interpreter; CI proves this in a job
  that installs nothing.
* Some indirection is unavoidable: a registry lookup instead of a direct
  import, an explicit DTO-to-schema mapping instead of returning domain objects
  from FastAPI. That mapping cost is accepted — it is what keeps the wire
  format from becoming the engineering model.
* Contributors cannot casually reach across layers; the failure is a red build
  with a message naming the offending import.
* Relaxing a rule means editing `LAYER_RULES`, which requires an ADR.

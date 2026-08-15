---
name: architecture
description: Layering, ports and adapters in openPDN. Read before adding a package, an importer, a solver, an endpoint or any cross-layer dependency.
---

# Architecture

openPDN is a modular monolith with a clean-architecture dependency direction.
The point is not tidiness: it is that IPC-2581 will not be the only input
format and the first fast solver will not be the last backend. Anything that
couples a user-facing feature to a particular file format or solver has to be
undone before the next one can be added.

openPDN is **format-independent**. IPC-2581 is the reference importer — the one
implemented first and tested against (ADR-0006) — and ODB++ is a planned second
adapter. Neither is privileged anywhere outside its own package.

## The layers

| Layer | Source root | May import | Must never import |
| --- | --- | --- | --- |
| Domain | `packages/domain/src` | stdlib only | everything else |
| Solver contract | `packages/solver-api/src` | domain | adapters, application, frameworks |
| Importer contract | `packages/pcb-import/src/openpdn/pcb_import/api.py` | domain | adapters, application, frameworks |
| Application | `packages/application/src` | domain, contracts | concrete adapters, FastAPI, Pydantic, env |
| Importer adapters | `packages/pcb-import/src/.../ipc2581/`, `.../canonical_json/` | domain, its own contract, third-party | application internals, apps |
| Solver adapters | `packages/solver-mock/src`, future backends | domain, solver contract, third-party | application internals, apps |
| Infrastructure | `packages/infrastructure/src` | domain, contracts, application, adapters | apps |
| HTTP API | `apps/api/src` | domain, contracts, application, infrastructure | the CLI, adapters directly |
| CLI | `apps/cli/src` | domain, contracts, application, infrastructure | the HTTP API, adapters directly |

Enforced by `tests/unit/test_architecture_boundaries.py`. It parses every
module — including `if TYPE_CHECKING` imports, which are not a loophole — and
fails on a wrong-direction edge.

## Acceptable and unacceptable dependencies

Prohibited — the domain must not know what any interchange format is. The rule
is general, and binds the reference format exactly as hard as the others:

```python
# packages/domain/src/openpdn/domain/board.py
from ipc2581 import LogicalNet         # NO
from odbdesign import Feature          # NO
import xml.etree.ElementTree as ET     # NO -- the domain does not know XML exists
import numpy as np                     # NO -- domain stays dependency-free
from pydantic import BaseModel         # NO -- the domain is not a wire format
```

> **External interchange formats terminate at the importer boundary.**

That one sentence is the reason for all of the above. A format that reaches the
domain has to be removed from it before the *next* format can be added.

Prohibited — application services must not know which backend runs:

```python
# packages/application/src/openpdn/application/analysis_service.py
from elmer import Solver                        # NO
from openpdn.solver.mock import MockSolver      # NO, even for a default
```

Correct — depend on the contract, resolve by name:

```python
from openpdn.solver.api import SolverRegistry   # contract only

class AnalysisService:
    def __init__(self, solvers: SolverRegistry, default_solver: str) -> None:
        self._solvers = solvers
```

Correct — adapters know the outside world and translate into domain types:

```python
# packages/pcb-import/src/openpdn/pcb_import/ipc2581/importer.py
from openpdn.domain.board import Board
from openpdn.pcb_import.api import ImportResult, MalformedSourceError
```

The adapter layout is one directory per format:

```text
packages/
└── pcb-import/
    ├── api.py            the contract: no format knowledge, no dependencies
    ├── ipc2581/          reference importer (in development)
    ├── canonical_json/   openPDN's own format: fixtures and golden snapshots
    └── odbpp/            planned
```

## Adding a solver

1. New package `packages/solver-<name>/src/openpdn/solver/<name>/`.
2. Implement `describe()` and `solve()` from `openpdn.solver.api`.
3. Report `SolverCapabilities` truthfully. If the study asks for physics you do
   not implement, raise `SolverUnsupportedFeatureError` — never silently solve
   a different problem.
4. Translate every backend exception into a `SolverError` subclass. A caller
   must never have to catch a third-party exception type.
5. Register it in `build_container`. That is the only file that learns its name.
6. Add validation cases (see the `testing` skill) before advertising it as
   physical; `tests/validation/test_solver_validation_gate.py` will fail until
   you do.

No application, API, CLI or UI file should change to add a backend. If one
does, the abstraction leaked.

## Adding an import format

1. New adapter directory under `packages/pcb-import/`, one per format.
2. Implement `PCBImporter`; return `ImportResult(board, diagnostics)`.
3. `can_load()` inspects the document, not the filename. An extension is a
   hint: `.xml` says nothing about which schema is inside.
4. Emit a `Diagnostic` for every repair, assumption and dropped feature. An
   importer that silently "fixes" geometry is worse than one that fails.
5. Populate `ImportResult.capability_report`, so a user learns what was and was
   not obtained rather than discovering it in a solver failure.
6. Register it in `build_container`. Importers register unconditionally; which
   one runs is decided per document, not per deployment.
7. Treat the source as hostile: see `SECURITY.md`, and for XML formats
   `.agents/skills/ipc2581-import/SKILL.md`.

An adapter still under construction stays registered with `available=False` and
a reason, so the UI can explain the gap instead of reporting a format the user
can see is supported as unrecognised.

Solvers must not change. If a new format requires a solver change, the
canonical model is missing a concept — extend the *model*, in the domain, with
an ADR.

## Studies, boards and results

* `Board` = what was manufactured. Immutable after import.
* `AnalysisStudy` = how it is being exercised: sources, loads, probes,
  material and thickness overrides, mesh settings, temperature.
* `ElectricalAnalysisResult` = what came back, with `fidelity`, `diagnostics`
  and `stats`.

A study never writes to a board. Overriding an unknown copper thickness creates
a `LayerThicknessOverride` on the study; the board keeps recording that the
value was absent. This is what lets one import serve many studies, and what
keeps provenance honest.

## Application services

An application service:

* orchestrates domain objects and ports;
* validates the request before doing expensive work
  (`study.validate_against(board)` first, always);
* logs structured events from `openpdn.application.events`;
* raises `ApplicationError` subclasses for user-caused failures.

An application service does **not**: read the environment, touch the network,
build adapters, format numbers for display, or know what HTTP is.

## Infrastructure

Everything OS-facing: configuration, logging, archive extraction, subprocess
execution, workspaces, registries and the composition root. Adapters here may
use any library. This is also where a persistence layer would go — when
persistence requirements exist, and not before.

## Surfaces

`apps/api` and `apps/cli` are thin. Both call the same services, which is why
`openpdn info` and `GET /api/info` agree by construction (there is a test).
A surface may format, parse and validate its own input; it may not compute an
engineering result.

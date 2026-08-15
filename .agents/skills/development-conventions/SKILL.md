---
name: development-conventions
description: How openPDN code is written -- units, naming, typing, errors, logging, configuration, subprocesses, docstrings, API and frontend conventions. Read before writing code.
---

# Development conventions

Rules specific to openPDN. Anything not stated here follows ordinary modern
Python/TypeScript practice.

## Units and physical values

**SI internally, always.** Metres, amperes, volts, ohms, siemens per metre,
kelvin, seconds. Millimetres, mils, ounces, milliohms, millivolts and A/mm²
exist only at boundaries: importers, the HTTP API, the CLI and the UI.

**Name raw floats with their unit.** A bare `thickness` is a defect waiting to
happen when the next caller passes micrometres.

```python
thickness_m: float
current_a: float
resistance_ohm: float
conductivity_s_per_m: float
current_density_a_per_m2: float
```

**Use `Quantity` wherever provenance matters** — anything imported, configured
or assumed. Use bare floats inside a numerical kernel, where the surrounding
code has already established the unit.

```python
thickness = Quantity.imported(34.8e-6, METRE)
plating = Quantity.assumed(25e-6, METRE, "IPC-6012 Class 2 minimum average")
```

`Quantity.require_unit(METRE)` before using a value as a number. It costs one
comparison and catches the argument-swap class of bug.

**No mystery constants.** Never:

```python
r = length / (5.8e7 * area)          # what material? at what temperature?
```

Instead:

```python
from openpdn.domain.materials import COPPER_CONDUCTIVITY_S_PER_M
r_ohm = length_m / (COPPER_CONDUCTIVITY_S_PER_M * area_m2)
```

Better still, take a `Material` and let temperature correction be explicit.

A unit-aware type system (pint, or a typed newtype layer) is worth revisiting
once the solver exists; do not add per-scalar runtime overhead to a matrix
assembly loop to get it.

## Typing

* `mypy --strict` passes. No `Any` in a signature without a comment saying why.
* Public functions are fully annotated; test functions need not be.
* Prefer `Protocol` for ports, `NewType` for identifiers (`NetId`, `LayerId`),
  `StrEnum` for closed vocabularies that cross the wire.
* Use `from __future__ import annotations` and put type-only imports under
  `if TYPE_CHECKING` — except where a framework resolves annotations at
  runtime. FastAPI does: dependency types must be imported normally, or the
  parameter is silently treated as a query parameter.

## Data classes and models

| Layer | Use |
| --- | --- |
| Domain | `@dataclass(frozen=True, slots=True)`, stdlib only |
| Application DTOs | `@dataclass(frozen=True, slots=True)` |
| HTTP request/response | Pydantic `BaseModel`, in `apps/api/.../schemas.py` |
| Configuration | `pydantic-settings`, in `infrastructure/config.py` |

Domain objects validate themselves in `__post_init__` and raise `DomainError`
subclasses. `slots=True` everywhere except where `cached_property` is needed
(`Board`, `Stackup`, `AnalysisStudy`, `ElectricalAnalysisResult`).

## Errors

* Domain: `DomainError` → `InvalidBoardError`, `InvalidStudyError`,
  `MissingPhysicalPropertyError`, ...
* Importers: `PCBImportError` → `UnsupportedFormatError`, `MalformedSourceError`.
* Solvers: `SolverError` → `SolverNotAvailableError`,
  `SolverUnsupportedFeatureError`, `SolverConvergenceError`, ...
* Application: `ApplicationError` → `ImportRequestError`, `AnalysisRequestError`.

Rules: adapters translate third-party exceptions at their boundary; never
`except Exception: pass`; never put untrusted file content in an error message;
`raise ... from exc` always.

`MissingPhysicalPropertyError` is preferred over a plausible default. Making
the user supply the number is the feature.

## Logging

Structured, event-keyed, via stdlib `logging`:

```python
_logger.info(
    events.PCB_IMPORT_FINISHED,
    extra={
        "event": events.PCB_IMPORT_FINISHED,
        "board_id": str(board.id),
        "net_count": len(board.nets),
        "duration_seconds": round(elapsed, 6),
    },
)
```

* Event names come from `openpdn.application.events`, never inline strings.
* Context is structured fields, not string interpolation.
* Never log credentials (the formatter redacts key-looking fields as a
  backstop, not a licence).
* Never log full PCB geometry: log counts, ids and durations. Boards are
  confidential and enormous.
* `logger.exception` only where the traceback is genuinely useful.

## Configuration

One module reads the environment: `infrastructure/config.py`. Everything else
receives values as arguments. `tests/unit/test_config.py` fails the build on a
stray `os.getenv`.

Precedence: defaults → TOML file → `.env` → environment → explicit arguments.
All variables are `OPENPDN_`-prefixed. Add a setting by adding a typed field,
documenting it in `.env.example`, and passing it down — never by reading the
environment where you need it.

## Filesystem and subprocesses

* `pathlib.Path`, never string concatenation (ruff `PTH` enforces it).
* Untrusted archives: `infrastructure.archives.safe_extract_zip` /
  `safe_extract_tar`. Never `extractall`.
* Untrusted names: `infrastructure.workspace.sanitise_label`. An uploaded
  filename must never determine a path.
* External tools: `infrastructure.process.run_tool` — argument lists,
  mandatory timeout, explicit cwd, minimal environment. Never `shell=True`,
  never an f-string command.

## Async

The API is async at the edges; application services are synchronous today
because nothing in them is I/O-bound. Do not make a function `async` to look
modern. When a solver becomes long-running, it belongs in a worker with a job
API — that is an ADR, not an inline change.

## Dependency injection

Constructor injection, no framework, no globals. Objects receive their
collaborators; the composition root assembles them. In FastAPI, use `Depends`
to reach the container built at start-up.

## Comments and docstrings

* Module docstrings say what the module is *for* and what it must not do.
* Public functions get a docstring with `Args`/`Returns`/`Raises` where they
  are not obvious. Google style; `D` rules are on.
* Comments explain *why*: a physical assumption, a standard being followed, a
  numerical caveat, a rejected alternative. Do not narrate the code.
* Cite standards where a number comes from one (IEC 60028, IPC-6012, IPC-2152).

## Public API of a package

`__init__.py` re-exports the intended surface with `__all__`. Importing a
private submodule across a package boundary is a smell; importing an adapter
module from application code is a build failure.

## HTTP API conventions

* Routes under `/api`, versioned by `API_VERSION` when a break is needed.
* Response models in `schemas.py`, mapped explicitly from DTOs.
* snake_case JSON, matching the Python names.
* Errors: `{"error": "<ExceptionClass>", "detail": "<message>"}` with the
  status mapping in `app.py` — 400 application, 422 import, 500 solver.
* Endpoints stay thin: parse, call a service, map the result.
* `/api/health` must stay cheap and side-effect free.

## Frontend conventions

* React function components, TypeScript strict, no `any`.
* Biome formats and lints (`npm run format`).
* All network access goes through `src/api/client.ts`; components never call
  `fetch`.
* Every displayed physical value shows its unit; every uncertain value shows a
  `ProvenanceBadge`.
* SI → engineering unit conversion happens in the component boundary, not in
  the backend.
* Numbers render through `QuantityValue`/`formatEngineering`: monospace,
  tabular figures, selectable, no thousands separators (they break
  copy-paste into a spreadsheet).
* No feature in the UI for a capability `/api/info` reports as `planned`.

## Commits

Imperative subject, one concern per commit, body explains why. Mention the ADR
when a commit implements one.

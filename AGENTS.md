# AGENTS.md

Read this before changing anything. It is short on purpose; the detail lives in
`.agents/skills/`.

## 1. What openPDN is

A **format-independent** platform for **DC electrical analysis of printed
circuit boards**: IR drop, current density, via current, terminal-to-terminal
resistance, resistive power loss, and eventually electrothermal and 3-D
analysis.

**IPC-2581 is the reference interchange format** — the one implemented first
and tested against (ADR-0006). ODB++ is a planned second importer. Neither is
privileged: the canonical board model is format-independent, and no solver can
tell which importer produced a board.

**It imports and reviews boards; it does not simulate anything yet.** The
repository holds the canonical board model, the working IPC-2581 importer
(secure parsing through structural and geometric extraction), the geometry
normaliser that produces solver-ready `(net, layer)` copper (ADR-0007), the
board review services, the HTTP API, the CLI (`inspect`, `validate-import`),
and the interactive review UI (ADR-0008). The first solver is the next
milestone.

Do not write code, docs or UI copy implying more. `/api/info` is the
authoritative capability list, and `HEADLINE_CAPABILITIES` in
`packages/application/.../info_service.py` is its single source of truth.

## 2. Dependency rules

```text
UI / CLI / API  ──►  Application Services  ──►  Domain Model
                            │                      ▲
                            ▼                      │
                     Contracts (ports)  ◄──  Infrastructure Adapters
```

Non-negotiable:

* The **domain** (`packages/domain`) imports the standard library and nothing
  else. No IPC-2581, XML, ODB++, padne, FYPA, Elmer, FastAPI, Pydantic, NumPy,
  SciPy, Shapely, SQL.
* The **application layer** depends on domain types and on *contracts*
  (`openpdn.solver.api`, `openpdn.pcb_import.api`) — never on a concrete
  importer, a concrete solver, or a web framework.
* Concrete adapters are named in exactly one file:
  `packages/infrastructure/.../container.py` (the composition root).
* Board data and study data stay separate. A study never mutates a board;
  study-supplied thicknesses and material overrides live on the study.
* **Interchange formats terminate at the importer boundary.** No IPC-2581 (or
  ODB++, or XML) type, name or concept may appear in the domain, the
  application layer, the solver contract, the result model or frontend logic.
  A solver must never be able to tell which format a board came from.

`tests/unit/test_architecture_boundaries.py` enforces all of this. If it fails,
the design is wrong, not the test. Changing `LAYER_RULES` requires an ADR.

## 3. Engineering rules that outrank convenience

* **Never invent a physical property.** Unknown copper thickness, plating
  thickness, conductivity or hole diameter is represented as
  `Quantity(..., Provenance.ASSUMED, note=...)` or raises
  `MissingPhysicalPropertyError`. Never a bare default.
* **SI internally.** Metres, amperes, volts, ohms, kelvin. Convert at
  boundaries only. Name raw floats with their unit: `thickness_m`,
  `current_a`, `resistance_ohm`.
* **No mystery constants.** `5.8e7` in an expression is a defect; use
  `COPPER_CONDUCTIVITY_S_PER_M` or a `Material`.
* **Never claim unvalidated capability.** A solver may only be advertised as
  physical once validation tests compare it against `tests/validation/analytical.py`.
  `MockSolver` returns `ResultFidelity.MOCK` and says so in a diagnostic.

## 4. Where the documentation is

| Question | File |
| --- | --- |
| Why is the code laid out this way? | `docs/architecture/overview.md` |
| Why was X decided? | `docs/decisions/` (ADRs) |
| What physics are we solving? | `docs/simulation/physics.md` |
| How do I run things? | `CONTRIBUTING.md` |
| What is untrusted input here? | `SECURITY.md` |
| How is IPC-2581 read? | `.agents/skills/ipc2581-import/SKILL.md` |

## 5. Skills — read the relevant one before you start

| Work you are doing | Read first |
| --- | --- |
| Anything touching layering, ports, adapters | `.agents/skills/architecture/SKILL.md` |
| **IPC-2581 import code (mandatory)** | `.agents/skills/ipc2581-import/SKILL.md` |
| Writing any Python or TypeScript | `.agents/skills/development-conventions/SKILL.md` |
| Adding or changing tests | `.agents/skills/testing/SKILL.md` |
| Board, stackup, nets, vias, studies | `.agents/skills/pcb-domain-model/SKILL.md` |
| Solvers, meshing, numerics, results | `.agents/skills/solver-development/SKILL.md` |
| Any UI work | `.agents/skills/frontend-ux/SKILL.md` |
| The board viewport / renderer | `.agents/skills/pcb-viewer/SKILL.md` |

`.agents/private/` is gitignored local infrastructure knowledge. Never commit
its contents, and never copy deployment hosts, endpoint ids or credentials from
it into tracked files.

## 6. Prohibited shortcuts

* Importing an adapter from the domain or the application layer.
* Letting XML, an IPC-2581 element or a format revision escape the importer.
* Parsing untrusted XML with anything but
  `openpdn.pcb_import.ipc2581.secure_xml.parse_secure`.
* Guessing an interchange-format revision, or assuming a unit when a document
  does not declare one.
* `if isinstance(solver, PadneSolver)` — or any branch on backend identity
  outside the composition root.
* Writing solver inputs back into the `Board`.
* Widening a numerical tolerance to make a validation test pass.
* Filling a missing physical property with a "reasonable" default.
* Adding an endpoint, CLI command or UI control for a capability that does not
  exist yet.
* `os.getenv()` outside `packages/infrastructure/.../config.py`.
* Shell-interpolated subprocess calls; use `infrastructure.process.run_tool`.
* `zipfile.extractall` / `tarfile.extractall`; use `infrastructure.archives`.
* Introducing Redis, Kafka, Celery, Kubernetes or a database without a
  demonstrated requirement and an ADR.
* Committing anything under `.deploy/`, `.env`, or `.agents/private/`.

## 7. Definition of done

A change is done when **all** of these hold:

1. `./scripts/check.sh` passes (ruff, ruff format, mypy strict, pytest, biome,
   tsc, vitest, vite build).
2. New behaviour has tests at the right level (unit / integration / validation
   — see the testing skill).
3. The architecture boundary test still passes without being edited.
4. Public functions and modules have docstrings saying *why*, not *what*.
5. Physical quantities carry units in their names and provenance in their types.
6. Any new assumption is surfaced as a `Diagnostic`, not buried in a log line.
7. Capability claims in README, `/api/info` and the UI still match reality.
8. An architectural decision is recorded in `docs/decisions/` if you made one.
9. No secret, host, token or internal endpoint entered a tracked file.

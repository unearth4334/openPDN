# Architecture overview

## Shape

openPDN is a modular monolith, and format-independent by construction:
IPC-2581 is the reference importer (ADR-0006), not a privileged one. One
process serves the API and the built frontend; one CLI drives the same
application services. Distribution, background workers and message queues are
absent because nothing has yet demonstrated a need for them.

```text
        ┌──────────────┐    ┌──────────────┐
        │   Web UI     │    │     CLI      │
        └──────┬───────┘    └──────┬───────┘
               │  HTTP             │  in-process
        ┌──────▼───────┐           │
        │  apps/api    │           │
        └──────┬───────┘           │
               └────────┬──────────┘
                        ▼
             ┌──────────────────────┐
             │ Application services │  describe · import · analyse
             └──────────┬───────────┘
                        │ depends on contracts only
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
  PCBImporter    ElectricalSolver     Domain model
   (contract)      (contract)      Board · Study · Result
        ▲               ▲
        │               │
  ipc2581 (ref.)     mock            ← concrete adapters, wired in
  canonical-json     padne (planned)   infrastructure/container.py
  odbpp (planned)    elmer (planned)
```

Dependencies point inward. The rules, and the test that enforces them, are in
[ADR-0001](../decisions/0001-clean-architecture.md) and
`.agents/skills/architecture/SKILL.md`.

## Source layout

| Path | Layer | Third-party imports |
| --- | --- | --- |
| `packages/domain` | Domain entities, value objects, units, materials | none |
| `packages/solver-api` | Solver contract, capabilities, registry protocol | none |
| `packages/pcb-import/.../api.py` | Importer contract | none |
| `packages/geometry/.../api.py` | Geometry-normalisation contract (ADR-0007) | none |
| `packages/application` | Use cases, DTOs, log event names | none |
| `packages/pcb-import/.../ipc2581` | IPC-2581 reference adapter (implemented, revision B) | allowed |
| `packages/pcb-import/.../canonical_json` | Canonical JSON adapter | allowed |
| `packages/geometry/.../shapely_engine` | Shapely normalisation engine | allowed |
| `packages/solver-mock` | Mock backend | allowed |
| `packages/infrastructure` | Config, logging, archives, process, workspace, registries, composition root | allowed |
| `apps/api` | FastAPI surface | allowed |
| `apps/cli` | argparse surface | allowed |

The whole monorepo installs as one distribution (`openpdn`) whose modules share
the PEP 420 namespace package `openpdn`. The layering is enforced by a test
rather than by wheel boundaries — see ADR-0001.

## Request paths

### `GET /api/health`

```text
HTTP → routes/system.py → ApplicationInfoService.describe()
     → SolverRegistry / ImporterRegistry (adapters, resolved at start-up)
     → HealthResponse
```

Cheap and side-effect free: it is what the container HEALTHCHECK and any load
balancer will poll.

### `openpdn import <file>`

```text
CLI → BoardImportService.import_board()
    → ImporterRegistry: which adapter recognises this document?
    → <adapter>.load()
    → domain Board (validated) + diagnostics
```

The service never learns which format was read. Adapters identify a document by
inspecting it, not by trusting its extension, so `OPENPDN_IMPORTER` defaults to
`auto` and users do not have to name a format openPDN can identify.

An adapter still under construction (today: `ipc2581`) stays registered with
`available=False` and a reason, and the service reports that reason rather than
calling a recognised format unrecognised.

### Analysis (contract in place, no physical backend yet)

```text
AnalysisService.run(board, study, solver_name)
    → study.validate_against(board)        fail before expensive work
    → SolverRegistry.get(name)
    → capability check (via model, fidelity)
    → solver.solve(board, study)
    → ElectricalAnalysisResult (fidelity, diagnostics, stats)
```

## Cross-cutting

**Configuration** — one typed `Settings` object, one module that reads the
environment, `OPENPDN_`-prefixed variables, precedence
defaults → TOML → `.env` → environment → explicit arguments. A test fails the
build on a stray `os.getenv`.

**Logging** — structured, event-keyed from `openpdn.application.events`, JSON
in production and text in development. Counts and identifiers only: never
credentials, never full PCB geometry.

**Untrusted input** — imported documents and archives are hostile until proven
otherwise. XML parsing is hardened inside the IPC-2581 adapter
(`ipc2581/secure_xml.py`); extraction, path handling, workspace isolation and
subprocess execution live in `packages/infrastructure`. See
[SECURITY.md](../../SECURITY.md).

**Caching** (designed, not yet implemented) — geometry normalisation, meshing
and matrix assembly are separate pipeline stages with separate cache keys, so
changing a source or load magnitude re-solves without re-importing or
re-meshing. See `.agents/skills/solver-development/SKILL.md`.

## Deliberately absent

A database (no persistence requirement yet), a job queue (no long-running solve
yet), authentication (single-user development deployments), and the accelerated
viewport (no geometry to draw yet). Each arrives with the requirement that
justifies it, and with an ADR if it changes the architecture.

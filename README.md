# openPDN

**Open-source PCB DC conduction analysis.**

openPDN is a format-independent platform for the DC electrical questions that
decide whether a power delivery network actually works, using **IPC-2581 as its
reference interchange format**:

```text
IPC-2581
  ↓
Canonical PCB electrical model
  ↓
2.5-D FEM sheet conduction
  ↓
Voltage / Current density / Resistance
```

> **Status: import and review, no simulation yet.** openPDN imports IPC-2581
> boards into its canonical model, derives solver-ready conductive geometry,
> and provides an interactive review UI — layers, nets, stackup, vias,
> diagnostics and simulation readiness. It does not yet solve anything: the
> 2.5-D solver is the next milestone. The capability table below is
> authoritative, and `/api/info` reports the same statuses at runtime.

---

## Capabilities

| Capability | Status |
| --- | --- |
| Canonical, format-independent board model | **Implemented** |
| Provenance tracking (imported / configured / assumed / derived) | **Implemented** |
| Canonical-JSON board import (fixtures, golden snapshots) | **Implemented** |
| Solver contract + registry, mock backend | **Implemented** |
| `/api/health`, `/api/info`, `openpdn` CLI | **Implemented** |
| IPC-2581 secure parsing, revision detection, unit normalisation | **Implemented** |
| IPC-2581 structural import (stackup, layers, nets, pads, vias, components) | **Implemented** |
| IPC-2581 conductive geometry reconstruction (strokes, arcs, contours, flashes) | **Implemented** |
| Geometry normalisation to solver-ready `(net, layer)` regions | **Implemented** |
| Interactive PCB review UI (viewport, stackup, vias, diagnostics, readiness) | **Implemented** |
| `openpdn inspect` / `openpdn validate-import` | **Implemented** |
| IR-drop / voltage maps | Planned |
| Current-density analysis | Planned |
| Via current | Planned |
| Effective terminal-to-terminal resistance | Planned |
| Resistive power-loss density | Planned |
| Resistance-contribution analysis | Planned |
| ODB++ import | Planned — second importer |
| Electrothermal / 3-D refinement (ElmerFEM) | Planned |

Nothing is promoted out of *Planned* or *In development* until it works end to
end, and no analysis is promoted until numerical validation tests pass against
closed-form references. See
[`.agents/skills/testing/SKILL.md`](.agents/skills/testing/SKILL.md).

### Input formats

**Reference input:** IPC-2581 revision B — the first implementation target, and
what the fixtures are written against ([ADR-0006](docs/decisions/0006-ipc2581-reference-import-format.md)).

**Planned inputs:** ODB++; Gerber plus connectivity data; native EDA adapters
where they earn their keep. Each is an adapter behind the same contract, and
none of them may require a change to the canonical model or to solver code.

---

## Why the architecture looks like this

Three decisions shape everything else:

1. **The domain knows nothing about IPC-2581, ODB++, XML, padne, Elmer, FastAPI
   or React.** Formats and solvers are adapters around a canonical model, so
   adding ODB++ import or an Elmer backend does not touch solver code,
   application services or the UI. See
   [ADR-0001](docs/decisions/0001-clean-architecture.md) and
   [ADR-0002](docs/decisions/0002-canonical-pcb-model.md).

2. **IPC-2581 is the reference format, not the model.** It is where
   implementation starts and where tests bite. Everything IPC-shaped stops at
   the importer boundary. See
   [ADR-0006](docs/decisions/0006-ipc2581-reference-import-format.md).

3. **openPDN never silently invents a physical property.** Copper thickness,
   plating thickness and conductivity are routinely missing from fabrication
   data, and an IR-drop number computed from a guess is an engineering hazard
   if it looks like a measured one. Every physical quantity carries its
   provenance — imported, configured, assumed or derived — from the domain
   model through to the UI.

```text
   IPC-2581        ODB++            ← inputs
   (reference)     (planned)
       │               │
       ▼               ▼
  IPC2581Importer  ODBPPImporter    ← adapters
       └───────┬───────┘
               ▼
     Canonical Board Model  +  Analysis Study
               │
               ▼
        Solver Interface             ← Fast 2.5-D FEM · ElmerFEM · Mock
               │
               ▼
       Common Result Model
               │
        ┌──────┼──────┐
        ▼      ▼      ▼
      WebUI   API    CLI
```

---

## Repository layout

```text
apps/
  api/            FastAPI surface (routes are thin; no engineering logic)
  cli/            `openpdn` command line, same services as the API
  web/            React + TypeScript + Vite engineering UI shell
packages/
  domain/         Board, Stackup, Net, Via, Study, Results — zero dependencies
  application/    Use cases: describe, import, review, analyse
  solver-api/     ElectricalSolver contract, capabilities, registry
  solver-mock/    Test double: solves nothing, says so loudly
  pcb-import/     PCBImporter contract; ipc2581/ and canonical_json/ adapters
  geometry/       GeometryNormalizer contract + Shapely engine (ADR-0007)
  infrastructure/ Config, logging, safe extraction, subprocess, composition root
tests/            unit / integration / validation / fixtures
docs/             architecture, decisions (ADRs), development, simulation
.agents/skills/   Operational rules for coding agents
```

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

openpdn info                       # deployment description and capabilities
openpdn importers                  # what can read a board, and what cannot yet
openpdn inspect path/to/board.xml  # import an IPC-2581 file and review it
openpdn serve                      # http://127.0.0.1:8000 — API + review UI

npm install
npm run dev --workspace apps/web   # UI on http://localhost:5173
```

Full instructions, including Docker: [CONTRIBUTING.md](CONTRIBUTING.md).

---

## The physics openPDN is being built to solve

Steady-state current conduction in copper:

$$\nabla \cdot (\sigma \nabla V) = 0, \qquad \vec{J} = -\sigma \nabla V$$

For thin copper the 3-D problem collapses to a sheet problem on each layer,

$$\nabla_{xy} \cdot (\sigma t \, \nabla_{xy} V) = 0$$

coupled between layers by via conductances. That 2.5-D formulation is the
planned fast solver; ElmerFEM is planned for volumetric and electrothermal
refinement behind the same contract. See
[`docs/simulation/physics.md`](docs/simulation/physics.md).

---

## Working on openPDN

Coding agents start at [AGENTS.md](AGENTS.md); humans start at
[CONTRIBUTING.md](CONTRIBUTING.md). Architectural rules live in
[`.agents/skills/`](.agents/skills/) and are enforced by
`tests/unit/test_architecture_boundaries.py`, which fails the build on a
forbidden import rather than leaving the layering to good intentions.

## License

Apache 2.0 — see [LICENSE](LICENSE).

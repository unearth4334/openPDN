# Changelog

All notable changes to openPDN are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the major version is 0, the public API — HTTP routes, CLI commands and
domain types — may change in any release.

## [Unreleased]

### Added — IPC-2581 import and board review (milestone 1)

* **IPC-2581 structural extraction** (`packages/pcb-import/.../ipc2581/`):
  a staged pipeline — secure parse → typed syntax model → semantic extraction
  (ADR-0007). Imports stackup (copper, dielectric, solder mask, with imported
  thicknesses), nets (with vendor placeholder-net handling), copper artwork
  (stroked lines/arcs/polylines resolved to outlines, contours with cutouts,
  flashed dictionary shapes, instance transforms with rotation/mirror/offset),
  board profile (including non-plated holes as cutouts), padstacks, vias with
  layer spans and drill diameters, pads, pins → terminals, and components.
  Unsupported constructs, degenerate features, negative-polarity artwork and
  every assumption surface as structured diagnostics; nothing is dropped
  silently, and no missing physical property is defaulted.
* **Geometry normalisation** (`packages/geometry`, ADR-0007): a
  `GeometryNormalizer` contract with a Shapely engine that unions imported
  copper per `(net, physical layer)` into disjoint, solver-ready regions,
  repairs invalid rings with a diagnostic, and preserves many-to-many
  provenance from normalised polygons back to source features.
* **Board review services**: `BoardReviewService` with derived layer Z
  positions, via span classification (through/blind/buried), via grouping,
  per-net and per-layer statistics, pipeline timings, and an LRU in-memory
  board store keyed by source digest + importer version + normaliser version
  (ADR-0009) — re-imports and UI interactions never re-parse.
* **HTTP API**: `POST /api/boards` (streamed, size-capped upload),
  `GET /api/boards`, `GET /api/boards/{id}`,
  `GET /api/boards/{id}/geometry?view=normalized|imported`, plus
  development-only fixture routes gated by `OPENPDN_DEV_FIXTURE`.
* **CLI**: `openpdn inspect` (structural review summary with readiness and
  timings) and `openpdn validate-import` (expectation checks via flags, built
  for private local fixtures).
* **Interactive review UI**: Canvas 2D board viewport (ADR-0008) with pan,
  zoom-at-cursor, fit, hover coordinates, hit-testing and selection; layer
  panel in physical order with visibility/solo/opacity; searchable net list
  with copper highlight and dimming; contextual inspector; tabbed review area
  (stackup cross-section + table, via summary/groups/table with viewport
  cross-probing, structured import diagnostics with simulation readiness,
  geometry statistics, source info); normalized/imported geometry views;
  drag-and-drop import with staged progress and actionable failure states.
* Committed IPC-2581 fixtures (`four-layer-stackup`, `via-through-board`,
  `plane-and-trace`, `negative-features`) with hand-checkable geometry, and
  ~100 new tests: arc tessellation and stroke-buffer analytics, transform
  semantics, Boolean-union semantics and provenance, extraction golden
  assertions, hostile-input paths, board API, CLI review commands, camera
  math, scene hit-testing and review-state reducer.

### Changed

* Shapely moved from the `solver` extra into runtime dependencies (the
  importer and the normaliser need it); `python-multipart` added for uploads.
* `Board` gained an optional manufactured `profile`; `CopperRegion` and `Via`
  gained optional `net_id` (unassigned copper is real), `source_ref`,
  `drill_diameter` and `padstack_name`; the canonical-JSON codec round-trips
  the new fields.
* `ImportResult` gained format-independent `ImportRunStats`.
* Architecture boundary rules gained the geometry contract/adapter rows
  (ADR-0007).

### Changed (pre-milestone realignment)

* **IPC-2581 is now the reference PCB interchange format** (ADR-0006). openPDN
  is described as format-independent; ODB++ becomes a planned second importer
  rather than the foundational input path. No OdbDesign dependency, container
  stage or service ever existed, so the change was to naming, documentation,
  configuration and capability claims — the canonical model, the importer
  contract and the solver architecture are untouched, which is what ADR-0001
  and ADR-0002 were for.
* `OPENPDN_ODB_BACKEND` replaced by `OPENPDN_IMPORTER`, defaulting to `auto`.
  Importers identify a document by inspecting it, not by its filename, so users
  no longer name a format openPDN can work out for itself.
* A recognised format whose adapter is not ready now reports *why*, instead of
  being reported as unrecognised.
* `CapabilityStatus` gained `IN_DEVELOPMENT`, so "started" is distinguishable
  from "usable" in `/api/info`, the CLI, the UI and the README.

### Added

* IPC-2581 adapter boundary layer (`packages/pcb-import/.../ipc2581/`):
  hardened XML parsing that refuses DTDs and entity declarations and bounds
  size, nesting depth and element count; revision detection with explicit
  refusal of unsupported revisions; SI unit normalisation at the boundary.
* `ImportCapabilityReport` on the importer contract, so an importer can state
  what it obtained and whether the board is simulation-ready.
* `.agents/skills/ipc2581-import/SKILL.md`, mandatory reading before touching
  import code.
* ADR-0006; hand-written IPC-2581B fixture and its fixture README; 58 further
  tests covering XXE, entity expansion, nesting, element-count and size limits,
  revision spellings, unit conversion and importer selection.

### Next milestone

The 2.5-D sheet-conduction solver: mesh one net's normalised regions, couple
layers through via conductances, apply a source and a load, and validate
against the analytical references. See `docs/architecture/roadmap.md`.

## [0.0.1] — 2026-08-14

Repository initialisation and architectural foundation. **openPDN does not
import ODB++ and does not simulate anything at this version.**

### Added

* **Domain model** (`packages/domain`, stdlib only): `Board`, `Stackup`,
  `Layer`, `Net`, `CopperRegion`, `Via`, `Pad`, `Terminal`,
  `PhysicalComponent`; `AnalysisStudy`, `VoltageSource`, `CurrentLoad`,
  `ResistanceProbe`, `MeshSettings`, `ViaModel`; `ElectricalAnalysisResult`,
  `TerminalResult`, `NetIRDropResult`, `ResistanceProbeResult`, `Diagnostic`,
  `ResultFidelity`, `SolverRunStats`.
* **Provenance-tagged quantities**: `Quantity` with imported / configured /
  assumed / derived provenance, mandatory notes on assumptions, and
  `MissingPhysicalPropertyError` instead of silent defaults.
* **SI unit conventions** with named material constants
  (`COPPER_CONDUCTIVITY_S_PER_M`, IEC 60028) and boundary conversion helpers.
* **Contracts**: `ElectricalSolver` / `StagedElectricalSolver` with capability
  declaration and a registry (`packages/solver-api`); `PCBImporter` returning
  `ImportResult` with diagnostics (`packages/pcb-import`).
* **Adapters**: canonical-JSON board importer and serialiser; `MockSolver`,
  which solves nothing and reports `ResultFidelity.MOCK` with a warning.
* **Application services**: deployment description, board import, analysis
  dispatch with pre-flight study validation and capability checking.
* **Infrastructure**: typed `OPENPDN_`-prefixed configuration with a documented
  precedence chain; structured JSON/text logging with event constants;
  traversal- and bomb-resistant archive extraction; isolated workspaces; a
  shell-free subprocess wrapper; the composition root.
* **HTTP API**: `/api/health`, `/api/info`, OpenAPI docs, error mapping, and
  optional static serving of the built frontend.
* **CLI**: `openpdn info | solvers | importers | import | serve`, sharing the
  API's services (with a test asserting the two agree).
* **Frontend**: React + TypeScript + Vite engineering shell — toolbar,
  layers/nets panel, viewport placeholder, property inspector, results console;
  provenance badges; dark and light themes.
* **Tests**: 154 backend tests across unit, integration and validation, plus 17
  frontend tests. Includes an architecture boundary test that fails the build
  on a wrong-direction import, a configuration test that fails on a stray
  `os.getenv`, and a validation gate that fails if a solver claims physics
  without validation cases.
* **Analytical reference library** for numerical validation: trace, sheet,
  series, parallel, via barrel, voltage division, IR drop, power, current
  density.
* **Packaging and CI**: multi-stage Dockerfile (non-root, health check, clean
  shutdown, ~170 MB); CI running lint, types, tests and a container smoke test
  on Python 3.12 and 3.13; GHCR publishing with SHA/branch/semver tags and
  build provenance.
* **Documentation**: `README`, `AGENTS.md`, six agent skills, ADRs 0001–0005,
  architecture overview, physics and validation notes, contributing guide and
  security policy. (At this version the roadmap anticipated ODB++ as the first
  importer; ADR-0006 subsequently made IPC-2581 the reference format.)

[Unreleased]: https://github.com/unearth4334/openPDN/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/unearth4334/openPDN/releases/tag/v0.0.1

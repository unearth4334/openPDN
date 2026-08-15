# Roadmap

Ordered by what unblocks the most. Each milestone ends with something a user
can run and a test that proves it.

IPC-2581 is the reference interchange format
([ADR-0006](../decisions/0006-ipc2581-reference-import-format.md)); ODB++ is a
planned second importer behind the same contract.

## Done — architectural foundation

Canonical board model, importer and solver contracts, application services,
HTTP API, CLI, UI shell, container image, CI, enforced layering. Plus the
IPC-2581 adapter's boundary layer: secure XML parsing, revision detection and
unit normalisation.

## Done — M1: IPC-2581 structural import

```text
IPC-2581B document
      ↓  secure parse (no DTD, no entities, bounded size/depth/elements)
   revision + units
      ↓  semantic extraction
Canonical Board Model
```

Acceptance criteria:

* format revision identified, unsupported revisions refused;
* units handled and normalised to SI at the boundary;
* layers imported, in the right order;
* stackup represented where the document provides it;
* nets imported;
* components imported;
* pads imported;
* vias imported, with drill and span information;
* board outline imported;
* every gap and assumption reported as a `Diagnostic`, and
  `ImportResult.capability_report` populated;
* golden canonical-JSON snapshot test over the minimal fixture.

All met; see `tests/integration/test_ipc2581_extraction.py`.

## Done — M2: conductive geometry reconstruction (with M1)

```text
IPC-2581 features
      ↓  transforms, rotation, mirroring, step-and-repeat
      ↓  positive and negative features, clearances, thermal relief
      ↓  boolean normalisation
CopperRegions grouped by (net, layer)
```

Acceptance criteria:

* visually correct conductive geometry against the fixtures;
* positive and negative features resolved correctly;
* pad and padstack geometry correct;
* via connectivity correct;
* **no format-specific geometry reaches the solver** — the boundary test and
  code review both check this.

Shapely entered behind the importer boundary and in the geometry-normalisation
layer (ADR-0007). Positive artwork, contour cutouts, transforms and flashes are
resolved; negative-polarity artwork is refused loudly (NOT_READY plus an error
diagnostic) until subtractive resolution lands. Step-and-repeat and thermal
reliefs gain support when a fixture demonstrates them.

## Done — M3: interactive PCB review UI

Canvas 2D viewport behind a scene-model boundary (ADR-0008): layers in
physical order with visibility/solo/opacity, searchable nets with highlight
and dimming, stackup cross-section, via review with cross-probing, structured
import diagnostics and simulation readiness, imported vs normalized geometry
views. A WebGL backend arrives with scalar-field overlays.

## Next — M4: first electrical vertical slice

```text
IPC-2581 board
    ↓  select net
    ↓  select source terminal
    ↓  select sink terminal
    ↓  apply a normalised 1 A
    ↓  2.5-D solve
effective terminal-to-terminal resistance
```

Validated against `R = L/(σwt)` with a stated tolerance and a convergence
check. Only then may an analysis capability move from *planned* to
*implemented*, and only then does `mock` stop being the default solver.

Then, in order: voltage map, current-density map, via current.

## M5 — multilayer and vias

Lumped via conductance coupling sheet domains; via current reported per via;
validated against parallel-barrel references.

## M6 — derived analyses

Resistive power-loss density, resistance-contribution ranking along a path, and
richer probe support.

## Later

* **ODB++ importer.** The second adapter, judged against the same canonical
  import tests wherever possible. Two independent importers of the same board
  are the cheapest way to catch semantic and geometry mistakes in either — and
  the real test of ADR-0002, since it must require no solver change.
* ElmerFEM backend for volumetric and electrothermal analysis, behind the same
  solver contract.
* Further inputs: Gerber plus connectivity data, native EDA adapters where they
  earn their keep.
* Persistence, once there is a requirement to keep boards and studies between
  sessions. A database before that is speculation.
* Long-running solves as jobs, once a solve is long enough to need it.

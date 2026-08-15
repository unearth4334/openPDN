# ADR-0002: A canonical PCB model between importers and solvers

## Status

Accepted — 2026-08-14. The decision stands. Which format is implemented *first*
changed in [ADR-0006](0006-ipc2581-reference-import-format.md): IPC-2581, not
ODB++. Nothing below needed to change for that, which is the point of it.

## Context

There will be several input formats. IPC-2581 (the reference format, per
ADR-0006), ODB++, Gerber plus drill data, and vendor formats all describe the
same physical object with different vocabulary. Meanwhile a solver needs very
little of what those formats carry: conductive geometry per layer, layer
thickness and material, via geometry, nets, and the points where the board
meets the outside world.

Separately, simulation inputs — a 0.85 V source, a 4 A load, a copper
conductivity at 85 °C, a mesh size — are not properties of the manufactured
board. Storing them on the board would mean re-importing to change a load, and
would destroy the distinction between what was fabricated and what was assumed.

## Decision

Define one canonical model in the domain and convert everything into it at
import time:

```text
IPC-2581 ─┐   (reference)
ODB++    ─┼──►  Canonical Board Model  ──►  Solver
Gerber   ─┤
Altium   ─┘
```

```text
Board
├── Stackup → Layer (function, index, thickness, material)
├── Net
├── CopperRegion  (net, layer, outline)
├── Via           (net, from/to layer, hole diameter, plating thickness)
├── Pad
├── Terminal      (where the board meets the outside world)
├── PhysicalComponent
└── ImportProvenance
```

Rules:

1. Format vocabulary stops at the importer. No IPC-2581, ODB++ or XML type,
   name or concept appears downstream.
2. `Board` is immutable after import and validates its own referential
   integrity.
3. Excitations live in `AnalysisStudy`, which references the board by id and
   never mutates it. An engineer-supplied thickness for an unknown layer is a
   `LayerThicknessOverride` on the study; the board keeps recording the gap.
4. Copper is normalised and grouped by `(net, physical layer)` before meshing.
   That grouping is derived solver-pipeline data, not part of the board.
5. `PCBImporter.load()` returns `ImportResult(board, diagnostics)` rather than
   a bare `Board`. Importers routinely repair outlines and stand in for missing
   thicknesses; those facts must reach the user, and the alternatives were to
   bury them in logs or to write them into the board.

## Consequences

* A new import format cannot require a solver change. If it does, the model is
  missing a concept — extend the model, with an ADR.
* One import serves many studies; changing a load current never re-reads
  fabrication data.
* Every importer pays for every field in the model, so fields are added only
  when a solver or a user-facing analysis needs one.
* The canonical model has a JSON serialisation
  (`openpdn.pcb_import.canonical_json`), which gives regression fixtures a
  reviewable format and every format importer a diffable golden-snapshot
  target.
* Concepts an interchange format carries but openPDN does not need are dropped
  at import, and the importer says so in a diagnostic rather than dropping them
  silently.

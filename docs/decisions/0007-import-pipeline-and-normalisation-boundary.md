# ADR-0007: Staged IPC-2581 extraction, and geometry normalisation as its own layer

**Status:** Accepted (2026-08-14)

## Context

Milestone 1 turns IPC-2581 documents into reviewable boards. Two structural
questions had to be settled before writing it:

1. How is the importer internally organised, so that revision quirks, vendor
   quirks and geometry math do not fuse into one unmaintainable parse function?
2. Where do Boolean operations on copper live? The canonical `Board` records
   *imported* copper — one region per source feature, overlapping, exactly as
   the artwork describes it. A solver meshes *normalised* copper — disjoint
   polygons per `(net, physical layer)`. Both views are needed: the review UI
   compares them, and the pcb-domain-model skill already ruled that normalised
   output is derived, cacheable data that never lives on the `Board`.

The application layer is dependency-free by rule, so Shapely cannot go there;
the importer adapter may use it, but normalisation is format-independent and
must serve every future importer identically.

## Decision

**Extraction is staged inside the IPC-2581 adapter**, each stage a module with
one job and XML terminating in the first:

```text
secure_xml.parse_secure   untrusted bytes -> bounded element tree
syntax.read_document      element tree    -> typed IPC-2581 syntax model (XML dies here)
extract.extract_board     syntax model    -> canonical Board + diagnostics + capability report
geometry.py               shared tessellation, transforms, stroke buffering (Shapely)
```

The syntax model keeps document units; scaling to metres happens exactly once,
in extraction. Unknown constructs are tallied by the reader and surfaced as
diagnostics — nothing is silently dropped. Stroked artwork is resolved to
outline polygons at import (a 0.5 mm trace is a 0.5 mm-wide region), so the
`Board` is already free of pending transforms and pending widths.

**Geometry normalisation is a separate architectural layer**,
`packages/geometry`, mirroring the importer/solver pattern:

* `openpdn.geometry.api` — the contract: `GeometryNormalizer`,
  `NormalizedGeometry`, `NormalizedRegion`. Domain imports only.
* `openpdn.geometry.shapely_engine` — the concrete engine, named only by the
  composition root. Unions copper per `(net, layer)`, repairs invalid rings
  with a diagnostic, and preserves many-to-many provenance from every
  normalised polygon back to the imported regions that produced it.

`LAYER_RULES` in the architecture boundary test gains the two rows; the
application layer may import `openpdn.geometry.api` and nothing deeper.

## Consequences

* A second importer (ODB++) reuses normalisation unchanged, and two importers
  reading the same board through one normaliser is the planned cross-check.
* The future solver consumes `NormalizedGeometry` — geometry it never has to
  repair — and cache keys already include the normaliser version, so an
  incompatible change cannot serve stale copper.
* The review UI can show imported and normalised views of the same board,
  which is how geometry bugs get *seen* rather than inferred.
* Cost: one more package and two more boundary rows to maintain. Accepted;
  the alternative was Shapely leaking into the application layer or
  normalisation welded into one importer.

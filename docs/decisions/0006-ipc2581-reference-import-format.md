# ADR-0006: IPC-2581 is the reference import format

## Status

Accepted — 2026-08-14.

Amends the *format choice* recorded in the Context of
[ADR-0001](0001-clean-architecture.md) and
[ADR-0002](0002-canonical-pcb-model.md), which anticipated ODB++ via OdbDesign
as the first importer. The decisions those ADRs record — the dependency
direction, and the canonical model between importers and solvers — are
unchanged and are what makes this amendment cheap.

## Context

openPDN was initialised expecting ODB++, read through OdbDesign, as the first
input format. Nothing was implemented against it: no OdbDesign dependency,
container stage or service was ever added, and the importer contract carries no
format knowledge. What existed was an assumption, expressed in naming,
documentation and a configuration variable.

Reconsidering that first target:

* **ODB++ needs an external toolchain.** OdbDesign is a C++ program, so the
  first import path would begin with a subprocess or a binding, a build, and a
  runtime dependency in the container — before openPDN has read a single net.
* **IPC-2581 is a single XML document against a published schema.** It can be
  parsed directly, in-process, with the standard library.
* **Minimal fixtures are writable by hand.** A two-layer IPC-2581 board is a
  reviewable file in a diff. A minimal ODB++ job is a directory tree, and a
  realistic one is a vendor export nobody can redistribute. Test quality
  depends on being able to write small, honest fixtures.
* **IPC-2581 is standards-oriented and vendor-neutral**, which suits a
  reference path that other importers get compared against.
* ODB++ remains necessary: it is what much of the industry actually ships. It
  is deferred, not dropped.

## Decision

**IPC-2581 becomes openPDN's reference interchange format and first
implementation target.** Revision B is the first supported revision.

**ODB++ remains a planned second importer**, behind the same `PCBImporter`
contract, judged against the same canonical import tests wherever possible.

Consequences for the architecture — all constraints, not permissions:

1. The canonical board model does **not** change to resemble IPC-2581. Where an
   IPC-2581 construct does not map exactly, the importer translates semantics.
2. No IPC-2581 type, name, XML element or format revision may appear in the
   domain, the application layer, the solver contract, the result model or
   frontend logic. `tests/unit/test_architecture_boundaries.py` names
   `ipc2581`, `xml`, `lxml` and `defusedxml` in its forbidden list for the pure
   layers.
3. Untrusted XML is parsed only through
   `openpdn.pcb_import.ipc2581.secure_xml.parse_secure`, which refuses DTDs and
   entity declarations outright and bounds size, depth and element count.
4. Units are normalised to SI at the importer boundary; an unknown or absent
   unit is refused, never assumed.
5. Revisions are detected once and refused when unsupported. Reading revision C
   with revision B semantics would produce a plausible, wrong board.
6. Importer selection is per document, not per deployment. `OPENPDN_IMPORTER`
   defaults to `auto`, and adapters identify a document by inspecting it rather
   than by trusting a filename.
7. The solver architecture is untouched. Solvers consume `Board` +
   `AnalysisStudy` and return `ElectricalAnalysisResult`, exactly as before.

## Consequences

Benefits:

* A simpler initial import path: direct, in-process parsing with no external
  toolchain and no extra container stage.
* Minimal, deterministic, redistributable fixtures that can be reviewed in a
  diff — which is what makes the importer testable at all.
* A standards-oriented reference path, vendor-neutral by construction.
* Fewer mandatory runtime dependencies in the default image.

Costs, stated plainly:

* **IPC-2581 does not remove the hard part.** Geometry normalisation — arcs,
  flashes, padstacks, step-and-repeat, transforms, positive and negative
  features, thermal relief — is still required, and it is still the bulk of the
  work. Choosing XML over a directory tree changes the parsing, not the
  semantics.
* Generators disagree. Real documents vary in which optional elements they
  populate and where they put units; robustness comes from fixtures and
  diagnostics, not from the schema.
* Revisions will need compatibility handling as A and C arrive.
* ODB++ coverage is deferred, and part of the industry ships ODB++ only. The
  eventual pay-off is that two independent importers of the same board become a
  cross-check on both.

## Alternatives considered

* **Keep ODB++ first.** Rejected: it front-loads an external toolchain and
  unwritable fixtures before any electrical result exists.
* **Implement both at once.** Rejected: two half-importers produce two sets of
  unvalidated semantics and no reference to compare against.
* **Define an openPDN-only input format and require conversion.** Rejected: it
  moves openPDN's hardest problem onto its users.

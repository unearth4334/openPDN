---
name: ipc2581-import
description: Mandatory reading before touching IPC-2581 import code. Covers the importer boundary, secure XML, revisions, units, geometry normalisation, connectivity, stackup, provenance and the import capability report.
---

# IPC-2581 import

IPC-2581 is openPDN's **reference** interchange format and first implementation
target (ADR-0006). Reference means "the one we implement first and test
against" — it does **not** mean the canonical model mirrors it, and it does not
make IPC-2581 special anywhere outside this adapter.

Status: implemented for revision B. The pipeline is staged (ADR-0007):
`secure_xml` (bounded parse of untrusted bytes) -> `syntax` (typed IPC-2581
syntax model; XML dies here) -> `extract` (semantic extraction to the
canonical board, diagnostics and the capability report), with `geometry.py`
holding tessellation, transforms and stroke buffering. Negative-polarity
artwork, step-and-repeat and thermal reliefs are refused/diagnosed rather
than resolved; they land with fixtures that demonstrate them.

## 1. The importer boundary

Everything IPC-2581-shaped stops at `packages/pcb-import/.../ipc2581/`.

No XML element, namespace, revision, `LogicalNet`, `LayerFeature`, `Xform` or
any other IPC vocabulary may appear in:

* the domain model
* application services
* the solver contract or any solver
* the result model
* the HTTP API, the CLI, or frontend logic

Prohibited, and the reason is general — external interchange formats terminate
at the importer boundary:

```python
# packages/domain/src/openpdn/domain/board.py
from ipc2581 import LogicalNet          # NO
import xml.etree.ElementTree as ET      # NO -- the domain does not know XML exists
```

```python
# packages/application/src/openpdn/application/analysis_service.py
from openpdn.pcb_import.ipc2581 import IPC2581Importer   # NO
```

The mapping direction is one-way:

```text
IPC-2581 LogicalNet  ──►  IPC2581Importer  ──►  openpdn.domain.Net
```

`tests/unit/test_architecture_boundaries.py` enforces this; `ipc2581`, `xml`,
`lxml` and `defusedxml` are named in its forbidden list for the pure layers.

## 2. XML is syntax, not the domain model

Do not build openPDN around XML nodes. An `Element` is a parsing artefact with
a lifetime of one function.

Bad:

```python
def solve(net_element: Element) -> None: ...
def area_of(feature: Element) -> float: ...
```

Good:

```python
board = importer.load(path).board      # canonical, format-free
solver.solve(board, study)
```

Do not "save time" by storing the parsed tree on the `Board`, and do not add a
`raw_xml` field. The board is what survives the importer; nothing else does.

## 3. Secure XML parsing

IPC-2581 files are untrusted input. Always parse through
`openpdn.pcb_import.ipc2581.secure_xml.parse_secure`. Never call
`ElementTree.parse`, `ElementTree.fromstring`, `minidom` or an `lxml` default
parser directly.

What `parse_secure` refuses, and why:

| Refused | Attack it closes |
| --- | --- |
| `<!DOCTYPE ...>` | XXE — `<!ENTITY x SYSTEM "file:///etc/passwd">` reads local files |
| `<!ENTITY ...>` | Entity-expansion bombs ("billion laughs"): a few hundred bytes → gigabytes |
| Documents over `max_bytes` | Memory exhaustion before parsing starts |
| Nesting over `max_depth` | Stack exhaustion in any recursive consumer downstream |
| More than `max_elements` | The "many small elements" bomb, which no byte limit catches |

Limits are enforced *during* the parse, so a hostile document is abandoned
early. Additional rules:

* Never resolve external references — no `xi:include`, no schema fetch, no
  network access of any kind during import.
* Never echo document content in an error message. Report *that* the document
  is malformed and where, never what was at that position. PCB data is
  confidential and error strings end up in logs, issues and screenshots.
* Zipped IPC-2581 packages, when accepted, go through
  `openpdn.infrastructure.archives` first — never `extractall`.
* A file name never determines a path. Stage into a `TemporaryWorkspace`.

Numeric hostility is a *semantic* concern, handled during extraction, not by
the parser: reject NaN and infinities, reject coordinates outside a plausible
board envelope, and reject geometry whose vertex count would produce a
pathological mesh. A valid XML document can still describe a board designed to
melt the solver.

## 4. Revisions

Revisions change semantics. Reading a revision C document with revision B rules
usually *succeeds* and produces a quietly wrong board — the worst possible
failure for an engineering tool.

* `IPC2581Revision` covers A, B and C; `SUPPORTED_REVISIONS` currently holds
  B alone.
* The revision is detected once, in `revision.py`, from the root element's
  `revision` attribute. Generators spell it `"B"`, `"b"`, `"IPC-2581B"`,
  `"2581-B"` — all are normalised.
* A missing revision is a hard failure. Defaulting to B would be a guess.
* An unsupported-but-known revision raises `UnsupportedRevisionError` with a
  message saying why guessing is unsafe.
* Revision branching stays inside the adapter. Never leak an
  `if revision is IPC2581Revision.C` into application code.
* Adding a revision means adding its semantic differences **and** fixtures —
  not just widening `SUPPORTED_REVISIONS`.

## 5. Units

An IPC-2581 document declares its units once, then gives bare numbers. openPDN
is SI internally (ADR-0004), so convert exactly once, here:

```text
IPC-2581 source        Canonical model
0.035 (MILLIMETER) ──► 3.5e-5 m
```

* `units.py` owns the conversion table. An unknown unit is refused, never
  assumed to be millimetres — that is the classic silent factor-of-25.4 error.
* A document declaring no units is refused: every dimension in it is ambiguous.
* Keep the source unit as *diagnostic* metadata where it helps a user ("0.035
  mm in your CAD tool"), but never let it travel with the value into the
  domain. Nothing downstream converts units.

## 6. Geometry normalisation

The solver consumes `CopperRegion`, never an IPC-2581 primitive. The importer
is responsible for resolving all of:

* lines, arcs, polygons and flashes
* padstack references and pad definitions
* repeated geometry (step-and-repeat) and instance transforms
* rotation and mirroring
* positive features (added copper) and negative features (removed copper)
* clearances and anti-pads
* thermal relief spokes

into physical conductive area. Rules:

* Apply transforms at import. A `CopperRegion` is in board coordinates,
  absolute, with no pending transform.
* Negative features are subtracted, not flagged for later. A region's outline
  and holes describe the copper that exists.
* Widths become area: a 0.5 mm polyline is a 0.5 mm-wide region, not a
  centreline plus an attribute.
* The end state is copper grouped by `(net, physical layer)` — the grouping a
  2.5-D sheet solver meshes over. See the `pcb-domain-model` skill.
* Report every repair, dropped feature and approximation as a `Diagnostic`. An
  importer that silently "fixes" artwork is worse than one that fails.

Shapely enters in two places, both behind boundaries: stroke-to-outline
resolution inside this adapter, and the per-(net, layer) Boolean union in the
geometry-normalisation layer (`packages/geometry`, ADR-0007). The importer
emits *imported* copper -- one region per source feature, transforms applied,
absolute coordinates; the normaliser derives the solver-ready view.

## 7. Connectivity

Map into canonical connectivity:

```text
LogicalNet   ──►  Net
Component    ──►  PhysicalComponent
Pin          ──►  Terminal (+ Pad)
Pad          ──►  Pad
Via/Drill    ──►  Via
Features     ──►  CopperRegion
```

**Do not assume XML validity implies electrical consistency.** A document can
declare a net that no copper touches, a pin on a net whose pad sits on another,
or two nets sharing overlapping copper. Where possible, validate declared
connectivity against reconstructed physical connectivity and report the
disagreement as a `Diagnostic` — the declared netlist and the artwork can and
do disagree, and that disagreement is often the bug the user is hunting.

## 8. Stackup and materials

Import whenever available: layer order, layer thickness, copper thickness,
dielectric thickness, material definitions, conductor material, drill
information, via spans.

Missing physical values must never become authoritative data. If the document
does not say how thick the copper is, the board records that it does not know
(`None`, or a `Quantity` marked assumed with a note). It does not record 35 µm
as though the fabricator said so.

## 9. Provenance

Every simulation-relevant property carries where it came from:

```text
Copper thickness      35 µm    IMPORTED
Via plating thickness 25 µm    DEFAULT_ASSUMPTION (IPC-6012 Class 2 minimum)
```

In code these are `Provenance.IMPORTED`, `CONFIGURED`, `ASSUMED` and `DERIVED`
(ADR-0004). An assumed quantity *must* carry a note explaining the assumption —
the constructor rejects it otherwise. The UI renders the difference, so an
IR-drop figure resting on a guess never looks like one resting on data.

## 10. The import capability report

`ImportResult.capability_report` (`openpdn.pcb_import.api`) is how an importer
says what it managed to obtain. It is format-independent by construction and
should eventually render as:

```text
IPC-2581 Import Report
────────────────────────────────
Format revision       IPC-2581B

Board outline         ✓
Copper geometry       ✓
Layer ordering        ✓
Net connectivity      ✓
Components            ✓
Pin mapping           ✓
Drill geometry        ✓
Copper thickness      ✓
Dielectric stackup    ✓
Via plating           ?

Simulation readiness: READY WITH ASSUMPTIONS
Warning: via plating thickness was not present.
```

Populate it as extraction lands — one `ImportCapabilityItem` per ingredient,
with `SimulationReadiness` derived from what is missing. A structurally valid
board that cannot be solved must say so here, not fail mysteriously three
layers down in a solver.

## 11. Where support ends today

Constructs the adapter recognises but does not resolve fail *loudly*, never
silently:

* Negative-polarity sets: the copper is not imported, an ERROR diagnostic is
  raised, and readiness is `NOT_READY` -- confidently wrong copper would be
  worse than a refusal.
* Unknown elements: tallied by the syntax reader and surfaced as
  `import.unsupported_construct` warnings with counts.
* Degenerate features (zero width, collapsed rings): counted and reported,
  never "repaired" into invented copper.

Two standing rules: never fabricate structure to make `load()` "work", and
never report a recognised format as unrecognised. Widening support means
adding the semantics *and* a fixture demonstrating them.

## 12. Testing

Fixtures live in `tests/fixtures/ipc2581/`, hand-written, minimal,
deterministic and redistributable — see that directory's README. Attack
payloads are built inside tests, never committed.

Golden snapshots compare an imported board against canonical JSON: counts,
ordering, names, bounds, connectivity. Not raw polygon dumps — they change for
uninteresting reasons and nobody can review the diff.

The same fixtures should eventually be importable by a second adapter (ODB++),
which is how semantic and geometry discrepancies between two independent
importers get caught.

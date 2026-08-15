# IPC-2581 fixtures

Hand-written IPC-2581 documents for importer tests. IPC-2581 is openPDN's
reference interchange format (ADR-0006), so this is where importer behaviour
gets pinned.

## Rules

* **Hand-written, not exported.** Nothing confidential is committed, every
  value is explainable, and a diff is reviewable.
* **Minimal and deterministic.** One fixture exercises one thing. No
  timestamps, GUIDs or generator noise that changes between runs.
* **Redistributable.** No vendor sample files, no customer boards.
* **Not production exports.** A regression suite built on one large real board
  tells you *that* something changed, never *what*. Large boards may be added
  later as opt-in slow tests, never as the foundation.

## Present

| Fixture | Exercises |
| --- | --- |
| `minimal-two-layer/board.xml` | Namespaced root, declared revision B, declared units, section layout, two conductive layers plus a core, stackup thicknesses, two nets, stroked polylines, one component, a logical netlist |
| `four-layer-stackup/board.xml` | Stackup ordering by sequence, layer-function classification, imported thicknesses, a deliberate missing thickness, material name references, zero-thickness non-physical layers |
| `via-through-board/board.xml` | Through/blind/buried via spans, drill diameters, dictionary pad shapes, via lands as copper, a pin pad producing a pad + terminal + component link, deliberately absent plating |
| `plane-and-trace/board.xml` | Contour with a cutout, round-ended stroke, full-circle arc stroke (annulus), rotated rectangular flash, two disjoint islands on one net, placeholder-net copper — all with hand-computable areas |
| `negative-features/board.xml` | Negative-polarity refusal (ERROR diagnostic + NOT_READY) and the unsupported-construct diagnostic |
| `degenerate-arc/board.xml` | Arc endpoint closure: exactly-coincident endpoints are a full circle, endpoints 12 nm apart are a rounded zero-length segment and must not sweep one |

## Planned

Added alongside the extraction they test — a fixture with no code reading it
proves nothing:

```text
thermal-relief/           spoked pad connections to a plane
step-and-repeat/          repeated instance geometry
```

## Golden snapshots

Structural imports are compared against canonical-JSON snapshots of the
resulting `Board` (see `.agents/skills/testing/SKILL.md`). Snapshots record
counts, ordering, names, bounds and connectivity — not raw polygon dumps, which
change for uninteresting reasons and are unreviewable in a diff.

## Hostile documents

XML attack payloads (XXE, entity expansion, deep nesting, oversized input) are
**generated inside the tests**, not committed. A repository full of live
exploit payloads is a hazard to anyone who opens it in the wrong tool, and the
tests are clearer when the attack is visible next to the assertion. See
`tests/integration/test_ipc2581_importer.py`.

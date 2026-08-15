# Test fixtures

Small, hand-reviewable inputs kept under version control so importer and solver
behaviour can be pinned over time.

## `boards/`

Boards in openPDN's canonical JSON format. They are written by hand, not
exported from a real design, so nothing confidential is committed and every
number in them is explainable.

| Fixture | What it exercises |
| --- | --- |
| `two-layer-rail.json` | Two conductive layers plus a core, two nets, three copper regions, two stitching vias, three terminals. Deliberately carries an *assumed* layer thickness, an *assumed* via plating thickness and one via with **unknown** plating, so provenance and missing-property handling stay covered. |

## Rules

* Fixtures stay small enough to read in a diff. If a case needs a large board,
  it belongs in a validation test with a generated geometry, not here.
* Never commit customer or NDA fabrication data. Anonymised is not sufficient
  if the geometry is recognisable.
* When a fixture changes, say why in the commit message: a fixture edit that
  makes a failing test pass is a regression being ratified.

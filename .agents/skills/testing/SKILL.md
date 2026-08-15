---
name: testing
description: openPDN's four test classes, how to choose one, and the tolerance discipline for numerical validation. Read before adding or changing tests.
---

# Testing

Four classes, four purposes. Put a test in the wrong one and it either runs too
slowly to be useful or proves less than it appears to.

| Class | Location | Marker | Answers |
| --- | --- | --- | --- |
| Unit | `tests/unit/` | `unit` | Does this pure code do what it says? |
| Integration | `tests/integration/` | `integration` | Does the boundary work — adapter, API, CLI, filesystem? |
| Validation | `tests/validation/` | `validation` | Is the number physically right? |
| Regression fixtures | `tests/fixtures/` | (used by the above) | Did behaviour on a known board change? |

Run: `pytest`, `pytest -m unit`, `pytest -m "not slow"`.

## Unit tests

Pure, fast, no I/O, no network, no subprocess. Domain invariants, value
objects, application services against stubs.

Application services are tested with hand-written stubs, not the real adapters
— that is the payoff of the port abstraction. See
`tests/unit/test_analysis_service.py`.

`tests/unit/test_architecture_boundaries.py` is a unit test that fails the
build on a wrong-direction import. Never edit it to make a change pass; if the
rules genuinely need to change, write an ADR first.

## Integration tests

One boundary at a time:

* importer adapter against a committed fixture;
* HTTP API through `TestClient`;
* CLI through `main(["..."])` with `capsys`;
* archive extraction and workspace isolation against real files.

Also test the *hostile* path at these boundaries: malformed XML, XXE and entity
expansion, traversing archive members, oversized and deeply nested documents. A
parser test that only feeds valid input tests half the parser — and for openPDN
the untested half is the security-relevant one.

### IPC-2581 fixtures

IPC-2581 is the reference interchange format (ADR-0006), so importer behaviour
is pinned against hand-written documents in `tests/fixtures/ipc2581/`:

```text
tests/fixtures/ipc2581/
├── minimal-two-layer/       present
├── minimal-single-layer/    planned
├── via-through-board/       planned
├── plane-and-trace/         planned
├── thermal-relief/          planned
├── component-pin-net/       planned
├── multiple-nets/           planned
├── stackup/                 planned
└── negative-features/       planned
```

Fixtures are **minimal, deterministic, redistributable** and understandable by
inspection. Rules:

* Hand-written, never exported from a CAD tool: nothing confidential is
  committed and every value is explainable in review.
* One fixture exercises one thing. No timestamps or generator noise.
* A fixture arrives with the extraction that reads it. A fixture no code reads
  proves nothing and rots.
* **Never** build the regression suite on one large production export. It tells
  you *that* something changed, never *what*. Large boards may later be opt-in
  `slow` tests; they are never the foundation.
* Attack payloads (XXE, billion laughs, deep nesting) are generated inside
  tests, not committed — a repository of live exploit files is a hazard, and
  the test reads better with the attack next to the assertion.

### Golden import tests

Structural imports are compared against a canonical-JSON snapshot of the
resulting board:

```text
fixture.xml ──► IPC2581Importer ──► Board ──► canonical JSON snapshot
```

Snapshot what a human would check in review: layer count and ordering, net
names, component and pad and via counts, board bounds, copper area per
`(net, layer)`, terminal connectivity.

Do **not** snapshot raw polygon dumps. They change for uninteresting reasons,
nobody can review the diff, and a failing test that cannot be read gets
regenerated rather than investigated — which converts a regression test into a
rubber stamp.

The same fixtures should eventually import through a second adapter (ODB++).
Two independent importers of the same board are the cheapest way to find
semantic and geometry mistakes in either.

Surface parity has its own test: the CLI and the API must report the same
deployment description, because they call the same service.

## Numerical validation tests

Geometries with closed-form answers, compared against
`tests/validation/analytical.py`:

* straight uniform trace: `R = L / (σ w t)`;
* sheet resistance: `R_□ = 1 / (σ t)`, and a square of copper equal to it;
* parallel conductors: `R = (ΣR_i⁻¹)⁻¹`;
* series segments: splitting a trace must not change its resistance;
* plated via barrel as a uniform annulus;
* voltage division along a series structure;
* `V = IR`, `P = I²R`, `J = I / A`.

The references themselves are pinned in
`tests/validation/test_analytical_references.py` — a validation suite whose
reference is wrong is worse than none.

### Tolerance discipline

**State a tolerance and justify it.**

* `rel=1e-12` — pure algebra, should agree to floating point.
* `rel=1e-3` — a hand-computed engineering value quoted to four figures.
* Anything looser needs a comment naming the physical reason: mesh
  discretisation error, a lumped via model versus a 3-D barrel, a spreading
  resistance the analytical form ignores.

**Never widen a tolerance to make a test pass.** If a solver drifts, either the
solver regressed or the model changed for a reason worth writing down. Loosening
`rel=1e-3` to `rel=1e-1` converts a validation suite into decoration. Prefer
refining the mesh in the test, or documenting the discretisation error as a
separate, explicitly-tolerated quantity.

**Test convergence, not just a single answer.** A solver claiming a physical
result should show the error falling as the mesh refines. A single passing
number can be a coincidence; a convergence trend cannot.

**Point sources are singular.** Current density diverges at a mathematical
point source. A test must not assert a finite current density at a terminal
node, and the solver must not report that singularity as a physical hot spot.

### The validation gate

`tests/validation/test_solver_validation_gate.py` fails if any registered
solver advertises physical fidelity without appearing in `VALIDATED_SOLVERS`.
Adding a real backend therefore breaks the build until its validation cases
exist. That is intentional — it is the mechanism behind "openPDN does not claim
what it has not validated".

## Regression fixtures

Committed boards in `tests/fixtures/boards/` (canonical JSON) and source
documents in `tests/fixtures/ipc2581/`, small enough to read in a diff and
hand-written so nothing confidential is committed. See `tests/fixtures/README.md`
and `tests/fixtures/ipc2581/README.md`.

Rules:

* Never commit customer or NDA fabrication data.
* A fixture keeps deliberate gaps (assumed thickness, unknown plating) so the
  missing-property paths stay exercised.
* Changing a fixture to make a failing test pass ratifies a regression. Say why
  in the commit message.

## Style

* Test names are sentences: `test_a_study_without_a_source_is_rejected`.
* Group with classes by behaviour, not by method name.
* One behaviour per test; `pytest.approx` for floats, never `==`.
* Comments in tests explain the *engineering* reason a case matters.
* Fixtures for shared setup live in `tests/conftest.py`.
* Frontend: Vitest + Testing Library, query by role/label, never by CSS class.

## What does not need a test

Getters, dataclass field access, and framework behaviour. Test *your*
invariants: the ones that, if broken, produce a wrong engineering answer.

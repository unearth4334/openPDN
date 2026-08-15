# Architecture decision records

Short records of decisions that are expensive to reverse. Each has **Status**,
**Context**, **Decision**, **Consequences** — and nothing else.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-clean-architecture.md) | Clean architecture with an enforced dependency direction | Accepted |
| [0002](0002-canonical-pcb-model.md) | A canonical PCB model between importers and solvers | Accepted |
| [0003](0003-solver-abstraction.md) | Solvers behind a contract, resolved by registry | Accepted |
| [0004](0004-si-units-internally.md) | SI units internally, provenance on every quantity | Accepted |
| [0005](0005-ghcr-container-deployment.md) | Ship a container image via GHCR; deploy pre-built | Accepted |
| [0006](0006-ipc2581-reference-import-format.md) | IPC-2581 is the reference import format; ODB++ is deferred | Accepted |
| [0007](0007-import-pipeline-and-normalisation-boundary.md) | Staged IPC-2581 extraction; geometry normalisation as its own layer | Accepted |
| [0008](0008-canvas-board-renderer.md) | Board viewport on Canvas 2D behind a scene-model boundary | Accepted |
| [0009](0009-board-store-and-geometry-transport.md) | In-memory board store keyed by content + pipeline versions; coarse geometry transport | Accepted |

## Writing one

Add `NNNN-short-title.md` with the next number. Write it when a decision
constrains future work: a layering rule, a boundary, a units convention, an
infrastructure dependency. Do not write one for a routine implementation
choice.

Superseding is normal: set the old ADR's status to `Superseded by ADR-NNNN`
and leave it in place. The record of what we thought at the time is the point.

Amendment is the lighter case: when a later ADR changes an *assumption* an
earlier one recorded but leaves its decision intact, note it in the older ADR's
status and move on. ADR-0006 amends ADR-0001 and ADR-0002 that way — the
layering and the canonical model survived the change of first import format
untouched, which is the strongest evidence those two decisions were right.

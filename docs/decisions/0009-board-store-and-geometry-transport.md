# ADR-0009: In-memory board store keyed by content and pipeline versions; geometry as one coarse payload

**Status:** Accepted (2026-08-14)

## Context

The review UI must never re-parse a board because the user toggled a layer,
selected a net or switched tabs. That requires (a) the backend keeping imported
boards and their derived geometry, and (b) an API shape that the frontend can
cache aggressively. It does not require a database — imports take fractions of
a second, boards are confidential uploads, and no persistence requirement
exists yet (AGENTS.md forbids adding one without an ADR that demonstrates it).

## Decision

**Store:** `BoardStore` is an application-layer port; the concrete store is an
in-memory, LRU-bounded dictionary in infrastructure (8 boards). A stored board
holds the import result, the normalised geometry and the stage timings.

**Identity:** the store key is
`sha256(source_digest : importer_version : normalizer_version)[:16]`.
Re-uploading identical content through the same pipeline is a cache hit; any
importer or normaliser version bump changes every key, so stale derived
geometry cannot survive an incompatible pipeline change unnoticed.

**Transport:** two coarse payloads instead of a chatty resource tree:

* `GET /api/boards/{id}` — the complete review (stackup, nets, vias, groups,
  diagnostics, readiness, statistics, timings). Small, fetched after import.
* `GET /api/boards/{id}/geometry?view=normalized|imported` — every renderable
  polygon of one view, SI metres, rings as `[x, y]` pairs. Large (~2 MB on the
  reference board), fetched once per view and cached client-side for the
  lifetime of the board.

Camera operations, visibility, highlighting and selection are pure frontend
state over those cached payloads; no interaction after load touches the
network.

## Consequences

* Restarting the process forgets imported boards. Acceptable for a review tool
  whose import costs under a second; a persistent store (the `/var/lib/openpdn`
  volume already exists for it) arrives with the requirement, and its schema
  will start from the same `StoredBoard` shape.
* Eight concurrent boards bounds worst-case memory near half a gigabyte on
  reference-class boards. The constant lives in one place.
* The geometry payload is deliberately dumb JSON. If profiling ever shows
  serialisation or transfer dominating, a binary/typed-array format slots in
  behind the same endpoint — that change would be measured first.

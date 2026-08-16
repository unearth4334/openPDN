# ADR-0011: Durable simulation jobs — SQLite queue, process workers, atomic artifacts

## Status

Accepted. Amended by ADR-0015: a Reference job freezes an adaptive *policy*
rather than resolved mesh numbers, because under goal-oriented adaptivity the
mesh is an output of the run. The queue, lease, retry and artifact decisions
below are untouched, and ADR-0015 preserves the reason this ADR froze absolute
numbers in the first place.

## Context

FEM solves take seconds to minutes and must never run inside FastAPI request
handlers. The queue must survive API/orchestrator/worker restarts and host
reboots; a partially written result must never be readable as complete;
cancellation must actually stop computation; and only infrastructure
failures may be retried automatically.

The spec suggested PostgreSQL. For the current single-host deployment that
adds a service, a driver dependency, credentials and a failure mode without
adding a guarantee: SQLite in WAL mode already provides transactional
claiming, durability across restarts, and multi-process access on one host.

## Decision

1. **Lifecycle**: `SimulationDraft` (untrusted) → validated + estimated →
   immutable `SimulationJobSpec` (absolute mesh numbers frozen in, not
   profile names) → `QUEUED`. Editing anything creates a new job. The
   `analysis_signature` hashes every solver-affecting input including solver
   version; duplicates of *active* jobs are coalesced, completed results are
   only ever reused by exact signature.
2. **Queue**: `JobStore` port; `SqliteJobStore` adapter (WAL,
   `UPDATE … RETURNING` claim, lease expiry, state machine enforced in SQL).
   A PostgreSQL adapter implements the same port when multi-host workers
   exist. States: QUEUED → CLAIMED → RUNNING → {COMPLETED,
   COMPLETED_WITH_WARNINGS, FAILED} plus CANCELLING/CANCELLED;
   `ALLOWED_TRANSITIONS` is the single source of truth.
3. **Execution**: `openpdn orchestrator` claims jobs and spawns
   `openpdn solver-worker --job-id …` subprocesses (structured argv, never a
   shell; numerical thread counts pinned via the sanctioned
   `process.worker_environment`). Workers heartbeat their own lease.
   Cancellation = SIGTERM, grace, SIGKILL, artifacts discarded.
4. **Retry policy**: a worker-*reported* failure (mesh error, disconnected
   terminals, missing property, singular system) is terminal. Only a
   silently-dead worker (expired lease) is requeued, below `max_attempts`.
5. **Artifacts**: filesystem store under the data dir. Boards are persisted
   as canonical JSON, content-addressed by digest, so workers never re-parse
   interchange formats. Results are written to `<job>.working/` and promoted
   with one atomic rename after the manifest validates; stale working
   directories are deleted on orchestrator start. NumPy archives are read
   with `allow_pickle=False`; job ids and digests are pattern-validated
   against traversal.
6. **Resource enforcement is server-side**: estimated DOFs above
   `worker_max_dofs` refuse the queue request. Accuracy is never silently
   lowered. Estimates come from the mesher's own sizing pass
   (`solver.fem.plan`), not a disconnected heuristic; connectivity is
   pre-checked on the region/via graph and re-verified exactly on the mesh.
7. **Resistance jobs drive the normalised 1 A test current through the
   published solve**, so field artifacts show the test-current distribution
   and the integrated loss equals `R` numerically at 1 A — an energy
   consistency check carried in every result's metrics.
8. **Verification profile** runs a second mesh with the feature-width sizing
   scaled by √2 and reports per-quantity relative changes plus a converged
   verdict against a 1 % target — evidence, not smaller triangles.

## Consequences

* No new infrastructure services in the deployment; the same container image
  runs `api`, `orchestrator` and `solver-worker` commands.
* Provenance: every result carries manifest.json (spec, signature, solver
  and normaliser versions, git-derived app version, mesh sizes, timings).
* The queue's guarantees are pinned by `tests/integration/test_job_store.py`
  (exclusive claim, legal transitions only, lease recovery with attempt cap,
  reported-failure-never-retried, reopen durability) and
  `test_simulation_artifacts.py` (atomic publication, traversal refusal).

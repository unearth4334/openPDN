---
name: simulation-jobs
description: The simulation job lifecycle — immutable specs, the durable queue, orchestrator/worker execution, artifact publication and retry rules. Read before touching job, queue, worker or result-storage code.
---

# Simulation jobs

## The one rule that outranks the rest

**Never run a solve inside an API request handler.** The API writes queue
rows; `openpdn orchestrator` claims them and spawns isolated
`openpdn solver-worker` subprocesses (ADR-0011). If a change makes a FastAPI
handler call `FemSheetSolver.solve`, the change is wrong.

## Immutable specs and signatures

* A queued `SimulationJobSpec` never changes. Editing a load, terminal,
  accuracy or material creates a *new* job.
* Specs store **resolved absolute mesh numbers**, not profile names, so a
  job re-run years later does not depend on that day's profile definitions.
* `analysis_signature` hashes every solver-affecting input, including the
  solver version. Reuse a result only by exact signature match — never
  because a display name matches.

## The state machine

`ALLOWED_TRANSITIONS` in `simulation_models.py` is the single source of
truth, and `SqliteJobStore.transition` enforces it *in SQL* — an illegal
transition cannot be written even by a buggy caller. Add states there first,
with tests, or not at all.

## Retry discipline

* A failure the worker **reports** (mesh error, disconnected terminals,
  missing physical property, singular matrix, convergence failure) is
  terminal: `FAILED`, diagnose, never auto-retry.
* Only a worker that **dies silently** (crash, OOM kill, reboot) leaves an
  expired lease; `recover_expired` requeues those below `max_attempts`.
* Do not blur this line. Auto-retrying numerical failures wastes compute and
  hides bugs.

## Artifact rules

* Results are written to `<job-id>.working/` and promoted with one atomic
  rename after the manifest parses. A partial result must never be readable
  as complete; stale `.working` directories are deleted on orchestrator
  start.
* Job ids and board digests are pattern-validated before touching the
  filesystem — persisted strings are input.
* `np.load(..., allow_pickle=False)` always. Result artifacts are data,
  never executable objects.
* Every result carries `manifest.json` provenance: spec, signature, solver
  and normaliser versions, mesh sizes, timings. A result without provenance
  is a bug.

## Resource enforcement

Frontend estimates are advisory; `SimulationService.queue` enforces
`worker_max_dofs` server-side and **refuses** over-budget jobs. Accuracy is
never silently lowered. Estimates must come from the mesher's own sizing
pass (`solver.fem.plan.count_mesh_points`) — an estimate from a disconnected
heuristic is fiction.

## Numerical conventions carried by jobs

* Resistance jobs drive the normalised 1 A probe current through the
  published solve: fields show the test distribution and integrated loss
  equals `R` numerically (energy consistency, checked in metrics).
* The Verification profile scales the *feature-width* sizing (not only the
  max/min bounds) by √2 for its comparison mesh — otherwise a width-graded
  mesh barely changes and the comparison proves nothing.
* `completed_with_warnings` is a real state: assumed plating, degraded point
  terminals or a failed convergence target must not present as clean.

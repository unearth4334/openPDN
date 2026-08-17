# ADR-0015: Reference job semantics — frozen targets, quality states, governance

## Status

Accepted. Amends ADR-0011, whose queue, lease, retry and artifact rules stand
unchanged; only what a Reference spec freezes is different, and the reason
ADR-0011 gave for freezing it is preserved.

## Context

ADR-0011 requires a `SimulationJobSpec` to store **resolved absolute mesh
numbers, not profile names**, so that "a job re-run years later does not depend
on that day's profile definitions." That rule assumes the mesh is an *input*.

Under goal-oriented adaptivity (ADR-0013) it is not. The mesh is the loop's
*output*: the run stops when the engineering quantity has converged or a
guardrail is hit, and the DOF count that took is unknown until it happens. A
Reference spec cannot name the mesh in advance without destroying the thing
that makes it Reference.

Two further problems follow. Queue-time DOF budgeting has nothing bounded to
check — the existing Verification profile already exposes a weaker form of this
(its refined comparison mesh is budgeted at queue time only because its size is
known in advance). And "completed" is no longer a sufficient outcome: a run
that exhausted its pass limit while still moving 0.4% per pass has finished
without converging, and must not present as a clean result.

## Decision

1. **A Reference spec freezes the complete adaptive policy, resolved to
   absolute values** — target QoI error, maximum passes, maximum DOFs, maximum
   memory, maximum wall-clock, element order, estimator parameters (`theta`,
   refinement ratio, feature floors), solver backend selection and the
   linear-tolerance ratio. It does not freeze a mesh.

   This is faithful to ADR-0011's actual rationale: everything that determines
   the outcome is still pinned to absolute numbers at queue time, and nothing
   depends on that day's profile definitions. What changed is that for an
   adaptive run the determining input is a policy, not a mesh.

2. **The signature hashes the frozen policy**, alongside board digest, study
   and solver version, exactly as today. The achieved mesh is an *output*,
   recorded in the manifest and never in the signature. Exact-match result
   reuse (ADR-0011) therefore still holds, and holds soundly, because the
   adaptive loop is deterministic (ADR-0013 §9): identical policy and identical
   inputs produce an identical result.

3. **No new job lifecycle states.** Reference quality is a property of the
   *result*, not of the queue, and `ALLOWED_TRANSITIONS` remains the single
   source of truth for the lifecycle. Quality states map onto states that
   already exist:

   | Result quality | Job state |
   | --- | --- |
   | `CONVERGED` | `COMPLETED` |
   | `CONVERGED_WITH_MODEL_LIMITATIONS` | `COMPLETED_WITH_WARNINGS` |
   | `RESOURCE_LIMITED` | `COMPLETED_WITH_WARNINGS` |
   | `NOT_CONVERGED` | `COMPLETED_WITH_WARNINGS` |
   | `NUMERICAL_FAILURE` | `FAILED` |

   `COMPLETED_WITH_WARNINGS` already exists for exactly this purpose — a
   finished job whose result must not present as clean. A Reference run that
   hit its pass or DOF ceiling while still moving is `RESOURCE_LIMITED`, and
   the UI must say so rather than showing a green tick.

4. **Four error sources are reported separately and never combined into one
   number**: geometry representation error, FEM discretisation error,
   linear-algebra error, and physical/model-form uncertainty. A converged mesh
   says nothing about an assumed via plating thickness. When the estimated
   discretisation error falls below the uncertainty in the physical inputs, the
   result states that plainly — the numerical solution being more precise than
   the model it is solving is information the engineer needs, not a defect to
   hide.

5. **Convergence is per quantity, and the overall state is derived.** Terminal
   resistance, load voltages and energy may converge while the sampled peak
   current density at a reentrant corner does not. A quantity tagged as
   singular (ADR-0013 §5) not converging must not by itself force
   `NOT_CONVERGED`; the run is `CONVERGED_WITH_MODEL_LIMITATIONS`, with the
   non-converging quantity named and explained.

6. **Extrapolation is conditional.** A Richardson-extrapolated value and its
   implied remaining error are published only after verifying the QoI sequence
   is monotonic and in an asymptotic regime and estimating the observed
   convergence order from the data. Non-nested meshes (ADR-0013) make this
   check mandatory rather than a formality. When the check fails, the
   finest-mesh value is reported alone.

7. **Every guardrail is enforced server-side**, regardless of what a client
   requested, against administratively configured ceilings. The existing rule
   holds: over-budget work is refused, never silently degraded.

8. **Reference jobs get their own priority class.** Claiming is FIFO today; a
   multi-hour Reference solve must not starve short interactive analyses.
   Reference and interactive jobs are scheduled as separate classes, and
   admission accounts for estimated memory rather than a flat process count,
   because one Reference job can consume a worker's entire RAM.

9. **Partial results survive cancellation.** Today a cancelled or timed-out job
   has its working directory discarded. A Reference run that completed three of
   six passes holds genuinely useful, already-paid-for work. Completed
   generations are checkpointed and a cancelled run may keep its latest valid
   generation — published as `NOT_CONVERGED`, labelled partial, never as a
   Reference-quality answer.

## Consequences

* `SimulationJobSpec` carries a Reference policy block; `analysis_signature`
  hashes it. Schema version increments, and the existing migration path
  (schema 1 to 2) is the precedent for keeping older rows loadable.
* Queue-time estimation reports the *initial* mesh and the policy ceiling, not
  a single predicted size. The UI must present a range and a ceiling rather
  than one number, since an honest estimate of the final size does not exist
  before the run.
* Checkpointing requires a versioned on-disk format for a completed generation
  (mesh, solution, indicators, QoI history, policy). Transient solver objects
  are never persisted; `allow_pickle=False` remains absolute.
* Retention needs a policy: always keep the final result, convergence history,
  final mesh and manifest; keep intermediate generations only when diagnostics
  were requested. Multi-million-DOF fields per generation are not kept by
  default.
* The retry rule is unchanged and matters more here: a worker-*reported*
  numerical failure is terminal and never auto-retried. Only silent worker
  death requeues — and with checkpoints, requeue can resume rather than restart.

## Measured (orchestration, 2026-08-16)

§7 (server-side ceilings) and §8 (priority classes, memory-aware admission)
are implemented. Three things are worth recording, one of them a defect this
work uncovered.

* **The worker had no Reference branch at all.** A Reference job could be
  drafted, validated, queued, claimed and executed -- and the worker would
  run it as a plain fixed-mesh solve, silently ignoring every knob in its
  policy and publishing the result under a Reference label. That is exactly
  the quiet degradation this tier exists to prevent, and it was invisible
  because nothing downstream checked. Adaptive specs now branch into the
  adaptive loop, and the outcome's quality state is published with the
  result.
* **Published fields must come from the final generation.** A first cut
  recovered them by re-solving, which lands on the *initial* mesh -- the
  result and the field data beside it would have described different meshes.
  `AdaptiveOutcome` now carries the final field data (verified: 589 triangles
  in both).
* **Ceilings are refused, not clamped.** Silently holding a run to a lower
  ceiling would make it report `RESOURCE_LIMITED` for a limit the user never
  chose, which is indistinguishable from the run genuinely needing more.
* **Admission control needs a claim it can hand back.** The orchestrator must
  claim a job to learn its size, so `release_claim` returns it to the queue
  and *decrements* the attempt counter -- that counter bounds execution
  retries, and a job deferred for memory never executed. Without the
  decrement, a repeatedly deferred job would exhaust its retry budget having
  never been tried once.
* The job table gained a `priority` column, which needed a real migration:
  `CREATE TABLE IF NOT EXISTS` does not backfill columns, and deployed stores
  hold live rows. Indexes over migrated columns are created after the
  migration runs, not in the same script.

**Not done: checkpointing (§9).** Completed adaptive generations are not yet
persisted, so a cancelled or requeued Reference run still restarts from
generation zero and `discard_working` still deletes partial state. The
generation history is published with a finished result, but resuming an
unfinished one is outstanding.

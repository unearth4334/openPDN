# .agents

Operational knowledge for coding agents working on openPDN.

```text
.agents/
├── skills/     version-controlled, project-wide rules  (committed)
└── private/    local infrastructure knowledge          (gitignored)
```

## skills/

One directory per skill, each with a `SKILL.md`. Read the relevant one before
starting work; `AGENTS.md` maps work to skills.

| Skill | Covers |
| --- | --- |
| `architecture` | Layering, ports, adapters, adding a solver or importer |
| `development-conventions` | Units, naming, typing, errors, logging, config, frontend |
| `testing` | The four test classes and tolerance discipline |
| `pcb-domain-model` | Board vs study, canonical concepts, copper normalisation |
| `ipc2581-import` | The reference importer: secure XML, revisions, units, geometry, connectivity |
| `solver-development` | Physics, 2.5-D formulation, pipeline stages, numerics |
| `frontend-ux` | Engineering-UI rules, density, units, provenance, heat maps |

Skills contain rules specific to openPDN. Generic software advice does not
belong here — it makes the file long enough that nobody reads the parts that
matter.

## private/

Gitignored. Holds knowledge about *this* development environment: deployment
targets, registry naming, orchestration endpoints. Nothing in it may be copied
into a tracked file, and it must never contain credentials — those belong in
the orchestrator's secret store or a gitignored `.deploy/`.

If `.agents/private/` is missing from your clone, that is expected: it is
local, per-environment knowledge and not part of the project.

# ADR-0005: Ship a container image via GHCR; deploy pre-built

## Status

Accepted — 2026-08-14.

## Context

openPDN needs a reproducible deployment unit. It has a Python backend, a built
frontend, and — in future — external solver binaries with their own system
dependencies. That combination is exactly what a container is for.

The repository lives on GitHub, so GitHub Actions and GitHub Container Registry
are available without adding an account, a credential or a service to the
project. The alternative failure mode is well known: an orchestrator that
builds from source at deploy time, producing an artefact nobody tested, at a
moment nobody is watching.

## Decision

**One image, built in CI, published to GHCR:**

```text
ghcr.io/<owner>/openpdn:<tag>
```

Tags: `sha-<full-git-sha>` on every push, `main`, `latest` on the default
branch, and semver tags for releases. Deployments use the immutable SHA tag or
the digest; `latest` and `main` are conveniences.

**Multi-stage build.** Node builds the frontend; a Python stage installs the
wheel into a virtualenv; the runtime stage carries only the interpreter, that
virtualenv and the built frontend. No build tools, test dependencies or source
tree in the runtime image (~170 MB).

**Runtime properties:** non-root user (uid 10001), `HEALTHCHECK` polling
`/api/health` with the interpreter already present (no curl, no shell),
`STOPSIGNAL SIGTERM` with uvicorn handling shutdown, and two declared volumes —
`/var/lib/openpdn` (persistent) and `/var/cache/openpdn` (regenerable).

**Backend and frontend ship as one unit.** The API serves the built frontend.
Splitting them is a deployment decision that would change one file.

**Building and deploying are separate.** `ci.yml` builds and smoke-tests an
image on every PR but publishes nothing. `publish.yml` publishes on pushes to
the default branch and on version tags, authenticating with the workflow's
`GITHUB_TOKEN`. No registry credential is stored in the repository.
Deployment to an environment is a separate, credentialed step, not a CI job.

**Deployed stacks never build from source.** A deployment stack references an
image; `docker-compose.example.yml` shows the shape, and environment-specific
values stay in a gitignored location.

## Consequences

* A deployed container is exactly the artefact CI tested, and a rollback is a
  tag change to an image that still exists.
* Provenance attestation is published with the image.
* No credentials, hostnames or orchestrator endpoints exist in the repository;
  that knowledge stays in gitignored local notes.
* Deploying is a manual step today. Automating it needs an API token and a
  written deployment policy; neither belongs here yet.
* Adding a system-level solver dependency (Elmer, a mesher) means editing the
  runtime stage, and the image will grow. Acceptable: the alternative is
  documenting installation steps nobody follows identically.
* Orchestrator specifics must never enter application code. openPDN does not
  know what runs it.

# Getting started

## Requirements

* Python ≥ 3.12
* Node ≥ 20 (24 is what CI and the image use)
* Docker (only for building the production image)

## Setup

```bash
git clone git@github.com:unearth4334/openPDN.git
cd openPDN
./scripts/bootstrap.sh
source .venv/bin/activate
```

`bootstrap.sh` creates `.venv`, installs the backend with dev extras and
installs frontend dependencies. It is safe to re-run.

## Run it

```bash
openpdn info                                  # deployment description
openpdn solvers                               # registered solver backends
openpdn importers                             # registered importers, and their status
openpdn import tests/fixtures/boards/two-layer-rail.json
openpdn serve                                 # http://127.0.0.1:8000/api/health

npm run dev --workspace apps/web              # http://localhost:5173
```

The Vite dev server proxies `/api` to `127.0.0.1:8000`, so run both to work on
the UI against a live backend.

## Checks

```bash
./scripts/check.sh              # everything CI runs
./scripts/check.sh backend
./scripts/check.sh frontend
```

Individually:

```bash
ruff check .            ruff format .
mypy                    pytest -q
pytest -m unit          pytest -m validation
npm run lint            npm run typecheck
npm test                npm run build
```

## Container

```bash
docker build -t openpdn:dev .
docker run --rm -p 8000:8000 openpdn:dev
curl -s localhost:8000/api/health
```

or `docker compose up --build` for the development stack. Deployment stacks run
pre-built GHCR images instead — see `docker-compose.example.yml` and
[ADR-0005](../decisions/0005-ghcr-container-deployment.md).

## Configuration

Copy `.env.example` to `.env` and edit. Every setting is a typed field in
`packages/infrastructure/src/openpdn/infrastructure/config.py`; nothing else in
the codebase reads the environment.

```bash
OPENPDN_LOG_LEVEL=DEBUG OPENPDN_LOG_FORMAT=json openpdn info
```

## Layout of a change

| Adding | Goes in |
| --- | --- |
| A domain concept | `packages/domain`, with unit tests |
| A use case | `packages/application`, tested against stubs |
| An import format | `packages/pcb-import/<format>/`, registered in the composition root |
| A solver | `packages/solver-<name>/`, registered in the composition root |
| An endpoint | `apps/api/src/openpdn/api/routes/`, thin |
| A CLI command | `apps/cli/src/openpdn/cli/main.py`, thin |
| UI | `apps/web/src/` |

Read `AGENTS.md` and the relevant `.agents/skills/*/SKILL.md` before making an
architectural change.

## Troubleshooting

**`ModuleNotFoundError: openpdn.domain`** — the editable install did not pick
up every source root. Re-run `pip install -e ".[dev]"`; the roots are listed
under `[tool.hatch.build.targets.wheel] dev-mode-dirs` in `pyproject.toml`.

**`test_architecture_boundaries` fails** — a dependency points the wrong way.
The message names the file and the import. Fix the dependency, not the test.

**Frontend cannot reach the API** — the backend is not running, or is not on
`127.0.0.1:8000`. The UI says "backend unavailable" rather than failing
silently.

**`openpdn import board.xml` says the file was recognised but no importer is
ready** — expected. The IPC-2581 adapter parses and identifies documents but
does not yet build a board; structural extraction is milestone 1. See
`docs/architecture/roadmap.md`.

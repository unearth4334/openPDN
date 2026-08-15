# Contributing to openPDN

> **Before making architectural changes, read [`AGENTS.md`](AGENTS.md) and the
> applicable files under [`.agents/skills/`](.agents/skills/).** They are short,
> they are specific to this project, and the architecture boundary test
> enforces a good part of what they say.

## Setup

Requirements: Python ≥ 3.12, Node ≥ 20, Docker (only to build the image).

```bash
git clone git@github.com:unearth4334/openPDN.git
cd openPDN
./scripts/bootstrap.sh          # .venv + backend dev extras + npm install
source .venv/bin/activate
```

Manually, if you prefer:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
npm install
```

## Running

```bash
openpdn info                                  # what this build can do
openpdn importers                             # what can read a board, and what cannot yet
openpdn import tests/fixtures/boards/two-layer-rail.json
openpdn inspect tests/fixtures/ipc2581/four-layer-stackup/board.xml
openpdn serve                                 # http://127.0.0.1:8000/api/health
npm run dev --workspace apps/web              # http://localhost:5173
```

## Checks

```bash
./scripts/check.sh              # everything CI runs, in CI's order
```

which is:

| Check | Command |
| --- | --- |
| Lint | `ruff check .` |
| Format | `ruff format --check .` |
| Types | `mypy` (strict) |
| Tests | `pytest` |
| Frontend lint | `npm run lint` (Biome) |
| Frontend types | `npm run typecheck` |
| Frontend tests | `npm test` (Vitest) |
| Frontend build | `npm run build` |

Auto-fix formatting with `ruff format .` and `npm run format`.

## Docker

```bash
docker build -t openpdn:dev .
docker run --rm -p 8000:8000 openpdn:dev
curl -s localhost:8000/api/health
```

## Where things go

| Adding | Package |
| --- | --- |
| Domain concept | `packages/domain` (stdlib only) |
| Use case | `packages/application` |
| Import format | `packages/pcb-import/<format>/` + one line in the composition root |
| Solver | `packages/solver-<name>/` + one line in the composition root |
| Endpoint | `apps/api/.../routes/` |
| CLI command | `apps/cli/.../main.py` |
| UI | `apps/web/src/` |

Adding a backend — importer or solver — must not require changing an
application service, an endpoint, a CLI command or the UI. If it does, the
abstraction leaked; fix that instead.

IPC-2581 is the reference interchange format (ADR-0006). Before touching import
code, read [`.agents/skills/ipc2581-import/SKILL.md`](.agents/skills/ipc2581-import/SKILL.md):
untrusted XML, format revisions and unit normalisation all have rules that are
not obvious from the code alone.

## House rules worth repeating

* SI units internally; convert at boundaries; put units in variable names.
* Interchange formats stop at the importer. No XML, IPC-2581 or ODB++ concept
  reaches the domain, the application layer, a solver or the UI.
* Parse untrusted XML only through `ipc2581.secure_xml.parse_secure`.
* No mystery constants — name them, and cite the standard they come from.
* Never invent a missing physical property. Mark it assumed with a note, or
  raise `MissingPhysicalPropertyError`.
* Never claim a capability that has not been validated.
* Never widen a numerical tolerance to make a test pass.
* No credentials, hostnames or deployment endpoints in tracked files.

## Tests

Four classes — unit, integration, validation, regression fixtures. Pick the
right one; `.agents/skills/testing/SKILL.md` explains how and why.

New behaviour needs tests. A bug fix needs the test that would have caught it.

## Architecture decisions

If your change constrains future work — a boundary, a units convention, a new
infrastructure dependency — add an ADR in `docs/decisions/` using the existing
format (Status, Context, Decision, Consequences). Keep it short.

## Pull requests

* One concern per PR; imperative commit subjects; body explains *why*.
* Say what you verified, and paste failing output if something is unfinished.
* Update `README.md` and the capability list if what openPDN can do changed.
* Mention the ADR your change implements, if any.

## Reporting problems

Open an issue with the openPDN version (`openpdn info`), what you ran, what you
expected and what happened. **Never attach confidential fabrication data.** If
a board is needed to reproduce, reduce it to a minimal hand-written canonical
JSON fixture — see `tests/fixtures/README.md`.

Security issues go to [SECURITY.md](SECURITY.md), not to a public issue.

## Verifying against a private local board

Confidential boards never enter the repository (see `.gitignore`: `/test.cvg`,
`/.local-fixtures/`, `/.local/` are ignored, and derived dumps belong under
`.local/`). To check the importer against one locally:

```bash
openpdn inspect path/to/board.cvg
openpdn validate-import path/to/board.cvg \
    --expect-conductive-layers <n> --expect-vias <n> --expect-nets <n>
```

Expectations are flags on purpose — nothing board-specific is committed. The
development UI can also import a local fixture with one click when
`OPENPDN_ENVIRONMENT=development` and `OPENPDN_DEV_FIXTURE` point at it.

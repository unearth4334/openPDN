#!/usr/bin/env bash
# Run every check CI runs, in the same order.
#
#   ./scripts/check.sh            backend and frontend
#   ./scripts/check.sh backend    backend only
#   ./scripts/check.sh frontend   frontend only
#
# A change is not done until this passes. See AGENTS.md.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

scope="${1:-all}"
venv_bin="$repo_root/.venv/bin"
failures=()

run() {
  local label="$1"
  shift
  echo
  echo "==> ${label}"
  if "$@"; then
    return 0
  fi
  failures+=("$label")
  return 0
}

if [[ "$scope" == "all" || "$scope" == "backend" ]]; then
  if [[ ! -x "$venv_bin/ruff" ]]; then
    echo "No .venv found. Run ./scripts/bootstrap.sh first." >&2
    exit 1
  fi
  run "ruff check"            "$venv_bin/ruff" check .
  run "ruff format --check"   "$venv_bin/ruff" format --check .
  run "mypy"                  "$venv_bin/mypy"
  run "pytest"                "$venv_bin/python" -m pytest -q
fi

if [[ "$scope" == "all" || "$scope" == "frontend" ]]; then
  if [[ -d node_modules ]]; then
    run "biome check"    npm run --silent lint
    run "tsc --noEmit"   npm run --silent typecheck
    run "vitest"         npm run --silent test
    run "vite build"     npm run --silent build
  else
    echo "!! node_modules missing; skipping frontend checks (run npm install)." >&2
  fi
fi

echo
if (( ${#failures[@]} > 0 )); then
  echo "FAILED: ${failures[*]}" >&2
  exit 1
fi
echo "All checks passed."

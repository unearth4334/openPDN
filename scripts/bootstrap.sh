#!/usr/bin/env bash
# Set up a development environment from a fresh clone.
#
#   ./scripts/bootstrap.sh
#
# Creates .venv, installs the backend with dev extras, installs frontend
# dependencies. Safe to re-run.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python_bin="${PYTHON:-python3}"
required_major_minor="3.12"

version="$("$python_bin" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [[ "$(printf '%s\n%s\n' "$required_major_minor" "$version" | sort -V | head -1)" != "$required_major_minor" ]]; then
  echo "openPDN needs Python >= ${required_major_minor}; found ${version}" >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "==> Creating .venv (Python ${version})"
  "$python_bin" -m venv .venv
fi

echo "==> Installing the backend with dev extras"
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -e ".[dev]"

if command -v npm > /dev/null 2>&1; then
  echo "==> Installing frontend dependencies"
  npm install --silent
else
  echo "!! npm not found; skipping the frontend. Install Node >= 20 to work on apps/web." >&2
fi

cat <<'EOF'

Ready. Next:

  source .venv/bin/activate
  openpdn info
  ./scripts/check.sh                     # everything CI runs
  openpdn serve                          # http://127.0.0.1:8000/api/health
  npm run dev --workspace apps/web       # http://localhost:5173

EOF

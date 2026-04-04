#!/usr/bin/env bash
set -euo pipefail
IFS=$' \t\n'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 13) else 1)'; then
  printf "Python 3.13 or newer is required.\n" >&2
  exit 1
fi

if [ -d ".venv" ] && [ ! -f ".venv/bin/activate" ]; then
  printf "Recreating broken .venv\n"
  rm -rf .venv
fi

if [ -f ".venv/bin/activate" ]; then
  printf "Using existing .venv\n"
else
  python3 -m venv .venv
fi

source .venv/bin/activate

pip install --upgrade pip
pip install --upgrade --editable ".[dev]"

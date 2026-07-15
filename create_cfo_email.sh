#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

PYTHON_BIN=""
for candidate in ".venv/bin/python" ".venv/bin/python3" ".venv/bin/python3.14"; do
  if [ -x "$candidate" ]; then
    PYTHON_BIN="$candidate"
    break
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "Missing a working .venv Python. Run ./scripts/bootstrap_env.sh first." >&2
  exit 1
fi

case " $* " in
  *" -h "*|*" --help "*)
    exec "$PYTHON_BIN" -m dynamix_manager.cli generate-cfo-email "$@"
    ;;
esac

echo "Generating CFO email. This can take a few minutes while TeamDynamix is refreshed..."
"$PYTHON_BIN" -m dynamix_manager.cli generate-cfo-email "$@"
echo "CFO email generation finished."

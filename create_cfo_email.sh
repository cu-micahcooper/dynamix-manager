#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

if [ ! -x ".venv/bin/python" ]; then
  echo "Missing .venv/bin/python. Run ./scripts/bootstrap_env.sh first." >&2
  exit 1
fi

case " $* " in
  *" -h "*|*" --help "*)
    exec ".venv/bin/python" -m dynamix_manager.cli generate-cfo-email "$@"
    ;;
esac

echo "Generating CFO email. This can take a few minutes while TeamDynamix is refreshed..."
".venv/bin/python" -m dynamix_manager.cli generate-cfo-email "$@"
echo "CFO email generation finished."

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

run_cfo_email() {
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
  PYTHONUNBUFFERED=1 "$PYTHON_BIN" -m dynamix_manager.cli generate-cfo-email "$@"
}

# Keep diagnostics private: API errors can contain internal URLs and identifiers.
umask 077
mkdir -p "$SCRIPT_DIR/logs"
LOG_FILE="$SCRIPT_DIR/logs/cfo_email_$(date -u +%Y%m%dT%H%M%SZ)_$$.log"
echo "CFO email log: $LOG_FILE"
printf 'Started: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LOG_FILE"

# tee keeps output visible; retain Python's exit status instead of masking it.
set +e
run_cfo_email "$@" 2>&1 | tee -a "$LOG_FILE"
STATUSES=("${PIPESTATUS[@]}")
set -e
STATUS="${STATUSES[0]}"
if [ "$STATUS" -eq 0 ] && [ "${STATUSES[1]}" -ne 0 ]; then
  STATUS="${STATUSES[1]}"
fi
printf 'Finished: %s; Exit status: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$STATUS" | tee -a "$LOG_FILE"
if [ "$STATUS" -ne 0 ]; then
  echo "CFO email generation failed. See $LOG_FILE" >&2
  exit "$STATUS"
fi
echo "CFO email generation finished."

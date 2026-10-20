#!/bin/bash
# scripts/run_batch_with_cleaning.sh
# Runs a REAL batch only if the watermark-cleaning service is healthy, then
# verifies that every job's files were actually finalized.
#
# Usage:  bash scripts/run_batch_with_cleaning.sh [extra run_batch.py flags]
#   e.g.  bash scripts/run_batch_with_cleaning.sh --test-mode
#
# Environment (all optional):
#   WATERMARKS_REMOVER_DIR  local checkout of the cleaning service
#                           (default: tools/watermarks-remover, which is git-ignored)
#   WATERMARKS_SERVICE_URL  default http://127.0.0.1:8765

set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WM_DIR="${WATERMARKS_REMOVER_DIR:-$ROOT/tools/watermarks-remover}"
URL="${WATERMARKS_SERVICE_URL:-http://127.0.0.1:8765}"
LOG="${TMPDIR:-/tmp}/wm-service.log"
STARTED_PID=""
cd "$ROOT" || exit 1

cleanup() {
  if [ -n "$STARTED_PID" ]; then
    kill "$STARTED_PID" 2>/dev/null
    echo "Stopped the cleaning service that this script started."
  fi
}
trap cleanup EXIT

healthy() { curl -s -m 3 "$URL/health" | grep -q '"ok": *true'; }

echo "== 1/4  Is the runtime context valid? =="
out=$(python3 tools/resume/v8/runtime_context_guard.py check 2>&1)
echo "$out"
echo "$out" | grep -q "RUNTIME CONTEXT: VALID" || { echo "STOP: fix the runtime context first (see docs/GETTING_STARTED.md)."; exit 1; }

echo
echo "== 2/4  Watermark-cleaning service =="
if healthy; then
  echo "Service is already running and healthy."
else
  case "$URL" in
    http://127.0.0.1:*|http://localhost:*) ;;
    *) echo "STOP: $URL is not reachable and is not a local address, so this script will not start it."; exit 1 ;;
  esac
  if [ ! -f "$WM_DIR/service/scripts/server.py" ]; then
    echo "STOP: cleaning service not found at $WM_DIR."
    echo "Clone the upstream project (see docs/GETTING_STARTED.md, 'Watermark cleaning'), or set WATERMARKS_REMOVER_DIR."
    exit 1
  fi
  PY=""
  for p in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$p"; break
    fi
  done
  [ -z "$PY" ] && { echo "STOP: the cleaning service needs Python 3.10 or newer."; exit 1; }
  PORT="${URL##*:}"
  echo "Starting the cleaning service with $PY on port $PORT ..."
  ( cd "$WM_DIR" && exec "$PY" service/scripts/server.py --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 ) &
  STARTED_PID=$!
  for i in $(seq 1 20); do healthy && break; sleep 1; done
  if ! healthy; then
    echo "STOP: the service did not become healthy. Last log lines:"
    tail -15 "$LOG"
    exit 1
  fi
  echo "Service is healthy."
fi

echo
echo "== 3/4  Running the batch =="
MARK=$(mktemp "${TMPDIR:-/tmp}/wm-batch-start.XXXXXX")
python3 -u tools/resume/v86/run_batch.py --input-dir jobs/batch_inbox --done-dir jobs/batch_done --max-workers 2 "$@"
RC=$?

echo
echo "== 4/4  Were all jobs actually finalized? =="
COUNT=0
BAD=0
while IFS= read -r d; do
  COUNT=$((COUNT+1))
  f="$d/watermark_finalization.json"
  if [ -f "$f" ] && grep -q '"overall_status": "FINALIZED"' "$f"; then
    echo "  FINALIZED      $(basename "$d")"
  else
    echo "  NOT FINALIZED  $(basename "$d")   <-- do not send these files"
    BAD=$((BAD+1))
  fi
done < <(find output -mindepth 1 -maxdepth 1 -type d -newer "$MARK")
rm -f "$MARK"

[ "$COUNT" -eq 0 ] && echo "  No job folders were created (was jobs/batch_inbox empty?)."
if [ "$BAD" -gt 0 ]; then
  echo
  echo "WARNING: $BAD job(s) were NOT finalized. Do not send those files."
  exit 2
fi
[ "$RC" -ne 0 ] && echo "The batch exited with code $RC."
echo "Done. All $COUNT job folder(s) were finalized."
exit "$RC"

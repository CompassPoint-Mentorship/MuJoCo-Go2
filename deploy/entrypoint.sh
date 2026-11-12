#!/usr/bin/env bash
# Start the simulator service, then JupyterLab.
#
# The service owns the dog. Notebooks connect to it via GO2_SIM_URL, and the
# split page shows it, so the view is live before anyone has run a cell and a
# kernel restart does not destroy the world.
set -euo pipefail

SIM_PORT="${GO2_SIM_PORT:-8399}"

# Keep the simulator running. If it ever dies, a dead panel and a notebook
# full of ConnectionRefusedError is a terrible experience for a student who
# did nothing wrong -- so supervise it and bring it straight back. The dog
# restarts at the origin, which is recoverable; a dead service is not.
supervise_simulator() {
  local attempt=0
  while true; do
    python -m go2.service --host 127.0.0.1 --port "$SIM_PORT"
    attempt=$((attempt + 1))
    echo "simulator service exited (restart #$attempt); restarting in 2s" >&2
    sleep 2
  done
}

supervise_simulator &
SUPERVISOR_PID=$!

trap 'kill "$SUPERVISOR_PID" 2>/dev/null || true' EXIT TERM INT

for _ in $(seq 1 60); do
  if python -c "
import sys, urllib.request
try:
    urllib.request.urlopen('http://127.0.0.1:${SIM_PORT}/state', timeout=1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
    echo "simulator service ready on 127.0.0.1:${SIM_PORT}"
    break
  fi
  if ! kill -0 "$SUPERVISOR_PID" 2>/dev/null; then
    echo "simulator supervisor died before the service came up" >&2
    exit 1
  fi
  sleep 1
done

exec jupyter lab \
  --ip=0.0.0.0 --port=8888 --no-browser \
  --ServerApp.root_dir=/home/student/work \
  --ServerApp.token="${JUPYTER_TOKEN:-}" \
  --ServerApp.password=

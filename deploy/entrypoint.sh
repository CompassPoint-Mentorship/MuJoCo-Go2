#!/usr/bin/env bash
# Start the simulator service, then JupyterLab.
#
# The service owns the dog. Notebooks connect to it via GO2_SIM_URL, and the
# split page shows it, so the view is live before anyone has run a cell and a
# kernel restart does not destroy the world.
set -euo pipefail

SIM_PORT="${GO2_SIM_PORT:-8399}"

python -m go2.service --host 127.0.0.1 --port "$SIM_PORT" &
SIM_PID=$!

# If the simulator dies, take the container down rather than leaving a Lab
# whose dog has silently vanished.
trap 'kill "$SIM_PID" 2>/dev/null || true' EXIT TERM INT

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
  if ! kill -0 "$SIM_PID" 2>/dev/null; then
    echo "simulator service failed to start" >&2
    exit 1
  fi
  sleep 1
done

exec jupyter lab \
  --ip=0.0.0.0 --port=8888 --no-browser \
  --ServerApp.root_dir=/home/student/work \
  --ServerApp.token="${JUPYTER_TOKEN:-}" \
  --ServerApp.password=

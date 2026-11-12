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
  local attempt=0 rc started delay
  while true; do
    started=$SECONDS
    # "|| rc=$?" is load bearing. Without it, `set -e` at the top of this
    # script terminates this whole loop the moment the service exits non-zero
    # -- which is every abnormal exit, including being killed -- so the
    # supervisor would silently die exactly when it is needed.
    rc=0
    python -m go2.service --host 127.0.0.1 --port "$SIM_PORT" || rc=$?
    attempt=$((attempt + 1))
    # Back off if it is failing immediately (bad port, broken install), so a
    # crash loop does not spin at 2s forever. A service that ran a while and
    # then died is a one-off; bring it straight back.
    if [ $((SECONDS - started)) -lt 5 ]; then
      delay=$(( attempt < 5 ? attempt * 5 : 30 ))
    else
      delay=2
    fi
    echo "simulator service exited (rc=$rc, restart #$attempt); restarting in ${delay}s" >&2
    sleep "$delay"
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

# Serve HTTPS when a certificate is mounted. Browsers only allow
# getUserMedia -- the microphone, for recording a student's voice -- on a
# secure context, and a self-signed certificate is enough for that once the
# student clicks through the warning. Without a cert we fall back to HTTP and
# everything except recording still works.
TLS_ARGS=()
if [ -r /etc/go2/tls/cert.pem ] && [ -r /etc/go2/tls/key.pem ]; then
  TLS_ARGS=(--ServerApp.certfile=/etc/go2/tls/cert.pem
            --ServerApp.keyfile=/etc/go2/tls/key.pem)
  echo "serving HTTPS (self-signed certificate)"
else
  echo "no certificate at /etc/go2/tls -- serving HTTP; microphone recording will be blocked" >&2
fi

exec jupyter lab \
  --ip=0.0.0.0 --port=8888 --no-browser \
  --ServerApp.root_dir=/home/student/work \
  --ServerApp.token="${JUPYTER_TOKEN:-}" \
  --ServerApp.password= \
  "${TLS_ARGS[@]}"

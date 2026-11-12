#!/usr/bin/env bash
# Manage per-student JupyterLab instances for the Go2 simulator.
#
#   ./go2-student.sh create <name>   allocate a port, token and work dir
#   ./go2-student.sh up     <name>   build (if needed) and start
#   ./go2-student.sh down   <name>   stop
#   ./go2-student.sh logs   <name>   follow logs
#   ./go2-student.sh link   <name>   print the student's click-to-open link
#   ./go2-student.sh list            everyone's port, link and status
set -euo pipefail
cd "$(dirname "$0")"
DEPLOY="$PWD"
ROOT="$(cd .. && pwd)"
STUDENTS="$DEPLOY/students"
WORKROOT="$ROOT/student-work"
mkdir -p "$STUDENTS" "$WORKROOT"

BASE_PORT=8400
CERT_DIR="$DEPLOY/tls"

# https only when a certificate is actually mounted, so the printed links
# always match what the container is serving.
scheme() { [ -r "$CERT_DIR/cert.pem" ] && echo https || echo http; }

# Prefer the tailnet address, fall back to the first LAN address, so the
# printed link works from wherever the students are.
host_ip() {
  if command -v tailscale >/dev/null 2>&1; then
    local ip; ip="$(tailscale ip -4 2>/dev/null | head -1 || true)"
    [ -n "$ip" ] && { printf '%s' "$ip"; return; }
  fi
  ip -4 -o addr show scope global 2>/dev/null \
    | awk '$2 !~ /^(docker|br-|veth|tailscale)/ {split($4,a,"/"); print a[1]; exit}'
}

# Every address this host answers on. A student may be on the office LAN or on
# the tailnet, so print both rather than guessing which link they can open.
host_ips() {
  local ts lan
  if command -v tailscale >/dev/null 2>&1; then
    ts="$(tailscale ip -4 2>/dev/null | head -1 || true)"
    [ -n "$ts" ] && printf '%s\ttailnet\n' "$ts"
  fi
  lan="$(ip -4 -o addr show scope global 2>/dev/null \
    | awk '$2 !~ /^(docker|br-|veth|tailscale)/ {split($4,a,"/"); print a[1]; exit}')"
  [ -n "$lan" ] && printf '%s\tlan\n' "$lan"
}

# One link per address, labelled.
#
# The link points at /proxy/8399/split -- the split page served by the
# simulator service through jupyter-server-proxy. It frames JupyterLab on the
# left and the live dog on the right, both same-origin, so the student opens
# ONE link and sees both straight away with nothing to arrange.
links_for() {
  # shellcheck disable=SC1090
  ( . "$1"
    host_ips | while IFS=$'\t' read -r ip kind; do
      printf '  %-9s %s://%s:%s/proxy/%s/split?token=%s\n' \
        "($kind)" "$(scheme)" "$ip" "$LAB_PORT" "${GO2_SIM_PORT:-8399}" "$LAB_TOKEN"
    done )
}

# Plain JupyterLab, without the split layout.
lab_links_for() {
  # shellcheck disable=SC1090
  ( . "$1"
    host_ips | while IFS=$'\t' read -r ip kind; do
      printf '  %-9s %s://%s:%s/lab?token=%s\n' "($kind)" "$(scheme)" "$ip" "$LAB_PORT" "$LAB_TOKEN"
    done )
}

# Single best-guess link, for the compact list view.
link_for() {
  # shellcheck disable=SC1090
  ( . "$1"; printf '%s://%s:%s/lab?token=%s\n' "$(scheme)" "$(host_ip)" "$LAB_PORT" "$LAB_TOKEN" )
}

usage() { sed -n '3,9p' "$0"; exit 1; }
[ $# -ge 1 ] || usage
CMD="$1"; shift || true

case "$CMD" in

create)
  [ $# -eq 1 ] || usage
  NAME="$1"; ENVF="$STUDENTS/$NAME.env"
  [ -e "$ENVF" ] && { echo "error: student '$NAME' already exists ($ENVF)"; exit 1; }

  shopt -s nullglob
  _existing=("$STUDENTS"/*.env)
  PORT=$((BASE_PORT + ${#_existing[@]}))
  TOKEN="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  WORK="$WORKROOT/$NAME"

  mkdir -p "$WORK"
  # The container runs as uid 1000; make sure it can write the student's work.
  chmod 2777 "$WORK"
  # Course material lives inside the package so that a local pip install on a
  # laptop gets the same notebooks this server hands out. -n never overwrites
  # a student's own edits.
  cp -n "$ROOT"/go2/coursework/* "$WORK"/ 2>/dev/null || true

  cat > "$ENVF" <<EOF
# JupyterLab + Go2 simulator for: $NAME
STUDENT=$NAME
LAB_PORT=$PORT
LAB_TOKEN=$TOKEN
STUDENT_WORK=$WORK
MUJOCO_GL=egl
EOF

  echo "Created student '$NAME'"
  echo "  work dir  $WORK"
  echo "  links:"
  links_for "$ENVF"
  echo
  echo "Start it with:  ./go2-student.sh up $NAME"
  ;;

up)
  [ $# -eq 1 ] || usage
  NAME="$1"; ENVF="$STUDENTS/$NAME.env"
  [ -e "$ENVF" ] || { echo "error: no such student '$NAME'. Run: ./go2-student.sh create $NAME"; exit 1; }
  docker compose --env-file "$ENVF" -p "go2-$NAME" up -d --build
  echo
  echo "Send $NAME whichever link matches their network:"
  links_for "$ENVF"
  echo
  echo "Plain JupyterLab, no split:"
  lab_links_for "$ENVF"
  ;;

down)
  [ $# -eq 1 ] || usage
  NAME="$1"; ENVF="$STUDENTS/$NAME.env"
  [ -e "$ENVF" ] || { echo "error: no such student '$NAME'"; exit 1; }
  docker compose --env-file "$ENVF" -p "go2-$NAME" down
  ;;

logs)
  [ $# -eq 1 ] || usage
  NAME="$1"; ENVF="$STUDENTS/$NAME.env"
  [ -e "$ENVF" ] || { echo "error: no such student '$NAME'"; exit 1; }
  docker compose --env-file "$ENVF" -p "go2-$NAME" logs -f
  ;;

link)
  [ $# -eq 1 ] || usage
  NAME="$1"; ENVF="$STUDENTS/$NAME.env"
  [ -e "$ENVF" ] || { echo "error: no such student '$NAME'"; exit 1; }
  links_for "$ENVF"
  ;;

list)
  printf '%-14s %-6s %-26s %s\n' STUDENT PORT STATUS TOKEN
  shopt -s nullglob
  for f in "$STUDENTS"/*.env; do
    # shellcheck disable=SC1090
    ( . "$f"
      st=$(docker ps --filter "name=go2-lab-$STUDENT" --format '{{.Status}}' | head -1)
      printf '%-14s %-6s %-26s %s\n' "$STUDENT" "$LAB_PORT" "${st:-stopped}" "$LAB_TOKEN" )
  done
  echo
  echo "Links are $(scheme)://<address>:<port>/proxy/8399/split?token=<token> on any of:"
  host_ips | while IFS=$'\t' read -r ip kind; do
    printf '  %s\t(%s)\n' "$ip" "$kind"
  done
  echo "Or: ./go2-student.sh link <name>"
  echo
  echo "GPU:"
  nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader | sed 's/^/  VRAM /'
  ;;

*) usage ;;
esac

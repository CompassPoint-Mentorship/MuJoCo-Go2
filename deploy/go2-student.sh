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
links_for() {
  # shellcheck disable=SC1090
  ( . "$1"
    host_ips | while IFS=$'\t' read -r ip kind; do
      printf '  %-9s http://%s:%s/lab?token=%s\n' "($kind)" "$ip" "$LAB_PORT" "$LAB_TOKEN"
    done )
}

# Single best-guess link, for the compact list view.
link_for() {
  # shellcheck disable=SC1090
  ( . "$1"; printf 'http://%s:%s/lab?token=%s\n' "$(host_ip)" "$LAB_PORT" "$LAB_TOKEN" )
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
  cp -n "$ROOT"/notebooks/*.ipynb "$WORK"/ 2>/dev/null || true

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
  echo "Links are http://<address>:<port>/lab?token=<token> on any of:"
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

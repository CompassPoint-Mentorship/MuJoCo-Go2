#!/usr/bin/env bash
# Manage per-student JupyterLab instances for the Go2 simulator.
#
#   ./go2-student.sh create <name>   allocate a port, token and work dir
#   ./go2-student.sh up     <name>   build (if needed) and start
#   ./go2-student.sh down   <name>   stop
#   ./go2-student.sh logs   <name>   follow logs
#   ./go2-student.sh link   <name>   print the student's click-to-open link
#   ./go2-student.sh list            everyone's port, link and status
#   ./go2-student.sh sync   <name>|--all [-n]   hand out updated coursework
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

# Handing out the course material is the engine's job, not this script's: the
# rules (replace what nobody touched, never overwrite an edit) have to be the
# same here as on a student's laptop, and two implementations of them would
# drift within a term. So this calls `go2-course sync`, which is the same code
# the laptop launcher runs.
#
# Preferring the repo's own venv keeps a clone working with no install; the
# PYTHONPATH fallback keeps it working with no venv either.
go2_python() {
  if [ -x "$ROOT/.venv/bin/python" ]; then
    printf '%s' "$ROOT/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    printf '%s' "$(command -v python3)"
  else
    return 1
  fi
}

seed_work() {   # seed_work <workdir> [dry]
  local work="$1" dry="${2:-}" py
  py="$(go2_python)" || die "no python3 on this host, so the course material
cannot be handed out. Install python3, or run: pip install -e $ROOT"
  PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$ROOT" "$py" -m go2.course \
      ${GO2_COURSE_PACK:+--course "$GO2_COURSE_PACK"} \
      sync --into "$work" ${dry:+--dry-run} \
    | sed 's/^/    /'
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() { sed -n '3,10p' "$0"; exit 1; }
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
  # laptop gets the same notebooks this server hands out.
  seed_work "$WORK"

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

sync)
  # Course material changes after students have started. `create` only seeded
  # once, so a notebook fixed today never reached anyone who already existed.
  DRY=""; ALL=""; TARGETS=()
  for a in "$@"; do
    case "$a" in
      -n|--dry-run) DRY=1 ;;
      --all)        ALL=1 ;;
      -*)           usage ;;
      *)            TARGETS+=("$a") ;;
    esac
  done
  if [ -n "$ALL" ]; then
    shopt -s nullglob
    for f in "$STUDENTS"/*.env; do
      TARGETS+=("$(basename "${f%.env}")")
    done
  fi
  [ ${#TARGETS[@]} -gt 0 ] || usage
  PY="$(go2_python)" || die "no python3 on this host"
  PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$ROOT" "$PY" -m go2.course \
      ${GO2_COURSE_PACK:+--course "$GO2_COURSE_PACK"} show | sed 's/^/  /'
  echo
  for NAME in "${TARGETS[@]}"; do
    ENVF="$STUDENTS/$NAME.env"
    [ -e "$ENVF" ] || { echo "error: no such student '$NAME'"; exit 1; }
    # shellcheck disable=SC1090
    WORK="$( . "$ENVF"; printf '%s' "$STUDENT_WORK" )"
    [ -d "$WORK" ] || { echo "error: $NAME has no work dir at $WORK"; exit 1; }
    echo "  $NAME   $WORK"
    seed_work "$WORK" "$DRY"
  done
  echo
  echo "Nothing to restart: the container mounts the work dir, so the files are"
  echo "already there. A notebook a student has open keeps the old copy in that"
  echo "tab until they close and reopen it."
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

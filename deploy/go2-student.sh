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

SEED=".go2-seed"

# Give one student the course material, without ever eating their work.
#
# $WORK/.go2-seed keeps a copy of exactly what we last handed over. That copy
# is the whole point: comparing it with the student's file says whether they
# have touched it, which `cp -n` cannot tell us -- and overwriting a notebook
# a student has spent an afternoon in is not a mistake you get to undo.
#
#   file missing      -> copy it in
#   same as the seed  -> never edited, so replace it
#   anything else     -> leave theirs alone, put the new one beside it as
#                        <name>.updated.<ext>, and say so
#
# Students created before the seed existed have no seed, so everything they
# have counts as edited. That is the safe way round to be wrong.
#
# Dotfiles stay out of JupyterLab's file browser, so .go2-seed is invisible.
seed_work() {
  local work="$1" dry="${2:-}" seedir="$1/$SEED"
  local src base dst sdst stem ext new
  local added=0 replaced=0 kept=0 current=0
  [ -n "$dry" ] || mkdir -p "$seedir"
  for src in "$ROOT"/go2/coursework/*; do
    [ -f "$src" ] || continue
    base="$(basename "$src")"
    dst="$work/$base"
    sdst="$seedir/$base"
    if [ ! -e "$dst" ]; then
      [ -n "$dry" ] || { cp "$src" "$dst"; cp "$src" "$sdst"; chmod 664 "$dst"; }
      echo "      + $base"
      added=$((added + 1))
    elif cmp -s "$src" "$dst"; then
      [ -n "$dry" ] || cp "$src" "$sdst"
      current=$((current + 1))
    elif [ -e "$sdst" ] && cmp -s "$sdst" "$dst"; then
      [ -n "$dry" ] || { cp "$src" "$dst"; cp "$src" "$sdst"; chmod 664 "$dst"; }
      echo "      ^ $base   (untouched, replaced)"
      replaced=$((replaced + 1))
    else
      case "$base" in
        *.*) stem="${base%.*}"; ext=".${base##*.}" ;;
        *)   stem="$base"; ext="" ;;
      esac
      new="$work/${stem}.updated${ext}"
      # The copy left beside theirs is a file a student will reasonably start
      # working in -- it is the fixed one. So it gets the same protection as
      # anything else of theirs: only ever written when it is absent, or
      # still exactly as we left it.
      if [ -e "$new" ] && [ -e "$sdst" ] && ! cmp -s "$new" "$sdst"; then
        echo "      ! $base   (yours kept; so is ${stem}.updated${ext}, which you have edited)"
      elif cmp -s "$src" "$new" 2>/dev/null; then
        echo "      ! $base   (yours kept; ${stem}.updated${ext} is already current)"
      else
        [ -n "$dry" ] || { cp "$src" "$new"; chmod 664 "$new"; }
        echo "      ! $base   (yours kept -- new one is ${stem}.updated${ext})"
      fi
      [ -n "$dry" ] || cp "$src" "$sdst"
      kept=$((kept + 1))
    fi
  done
  printf '      %d added, %d replaced, %d kept as-is, %d already current\n' \
    "$added" "$replaced" "$kept" "$current"
}

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
  [ -n "$DRY" ] && echo "(dry run: nothing is written)"
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

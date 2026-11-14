#!/usr/bin/env bash
# Pack the platform for moving or keeping.
#
#   tools/pack.sh                # code + docs: everything needed to rebuild it
#   tools/pack.sh --with-state   # also the state: secrets and student data
#
# The code pack is `git archive` of the current commit, so it is exactly what
# is committed: no secrets, no student work, nothing that only exists on this
# disk. Uncommitted changes are NOT in it -- commit first; this says so.
#
# The state pack is everything the running platform has accumulated that is
# not in git: .env, the classroom database, the class's course pack, hand-ins,
# student work, the hub's database, the old labs' env files and TLS files. It
# holds passwords and children's work, so it is written mode 600, kept
# separate from the code pack, and the script says so out loud.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="$ROOT/dist"
STAMP="$(date -u +%Y%m%d)"
SHA="$(git rev-parse --short HEAD)"
WITH_STATE=0
[ "${1:-}" = "--with-state" ] && WITH_STATE=1

mkdir -p "$OUT"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "note: there are uncommitted changes; the pack contains commit $SHA without them" >&2
fi

CODE="$OUT/robo-dog-$STAMP-$SHA.tar.gz"
git archive --format=tar.gz --prefix="robo-dog/" -o "$CODE" HEAD
echo "code + docs  $CODE  ($(du -h "$CODE" | cut -f1), commit $SHA)"

if [ "$WITH_STATE" -eq 1 ]; then
  STATE="$OUT/robo-dog-state-$STAMP.tar.gz"
  candidates=(.env classroom/classroom.db courses handins student-work
              deploy/hub/data deploy/students deploy/tls)
  list="$(mktemp)"
  trap 'rm -f "$list"' EXIT
  skipped=()
  for item in "${candidates[@]}"; do
    [ -e "$item" ] || continue
    while IFS= read -r -d '' f; do
      if [ -r "$f" ]; then
        printf '%s\0' "$f" >> "$list"
      else
        skipped+=("$f")
      fi
    done < <(find "$item" -type f -print0)
  done
  ( umask 077; tar --null -czf "$STATE" -T "$list" )
  chmod 600 "$STATE"
  echo "state        $STATE  ($(du -h "$STATE" | cut -f1), mode 600)"
  for f in "${skipped[@]}"; do
    echo "  skipped (not readable without sudo): $f" >&2
  done
  echo
  echo "  The state pack contains PASSWORDS and STUDENTS' WORK."
  echo "  Keep it encrypted, off shared drives, and never commit it."
fi

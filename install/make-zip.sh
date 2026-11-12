#!/usr/bin/env bash
# Build the file students download: Go2-Lab-macOS.zip
#
# The zip holds a double-clickable installer and a note about Gatekeeper --
# not the simulator itself. The installer fetches the current code from the
# public repo, so this zip never goes stale and never needs rebuilding when
# the simulator changes.
#
# Attach the result to a GitHub release:
#   gh release create v1 install/Go2-Lab-macOS.zip \
#      -t "Go2 Lab for macOS" -n "Download, unzip, read READ ME FIRST."
set -euo pipefail
cd "$(dirname "$0")"
OUT="${1:-$PWD/Go2-Lab-macOS.zip}"

# Written with Python rather than `zip` so the executable bit on the .command
# is set explicitly. Without it macOS opens the installer in TextEdit, which
# looks exactly like the thing not working.
python3 - "$OUT" <<'PY'
import pathlib, sys, zipfile

out = pathlib.Path(sys.argv[1])
here = pathlib.Path.cwd()   # make-zip.sh cd-s here first
files = {
    "Go2 Lab/Install Go2 Lab.command": (here / "Install Go2 Lab.command", 0o755),
    "Go2 Lab/READ ME FIRST.txt":       (here / "READ ME FIRST.txt",       0o644),
}
out.unlink(missing_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for name, (src, mode) in files.items():
        info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = (mode | 0o100000) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(info, src.read_bytes())
print(f"wrote {out} ({out.stat().st_size} bytes)")
for i in zipfile.ZipFile(out).infolist():
    print(f"  {oct(i.external_attr >> 16)[-4:]}  {i.file_size:>6}  {i.filename}")
PY

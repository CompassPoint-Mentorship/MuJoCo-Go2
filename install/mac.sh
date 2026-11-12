#!/bin/bash
# Install the Go2 simulator on a Mac (or a Linux laptop), for a student.
#
#   curl -fsSL https://raw.githubusercontent.com/CompassPoint-Mentorship/MuJoCo-Go2/main/install/mac.sh | bash
#
# Run it again any time to upgrade; it is idempotent.
#
# Design notes, because every one of these is load bearing:
#
#  * It installs NOTHING system-wide and never asks for a password. Everything
#    lands in ~/.go2 and ~/Applications, both of which the student owns.
#  * Python comes from `uv`, which downloads a standalone build. Reaching for
#    the system `python3` on a clean Mac pops the "install command line
#    developer tools" dialog, which is a dead end for a 14-year-old.
#  * The package is fetched as a GitHub *tarball*, not git+https, for the same
#    reason: a clean Mac has no git either.
#  * The .app is written HERE, on this machine, so it carries no quarantine
#    attribute and Gatekeeper never challenges it. A downloaded .app would be
#    blocked outright unless we paid Apple to notarise it.
set -euo pipefail

REPO="CompassPoint-Mentorship/MuJoCo-Go2"
BRANCH="${GO2_BRANCH:-main}"
# GO2_TARBALL lets a mentor point this at a local copy or an internal mirror,
# which is also how the installer gets tested before the code is pushed.
TARBALL="${GO2_TARBALL:-https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz}"
PREFIX="${GO2_PREFIX:-$HOME/.go2}"
VENV="$PREFIX/venv"
PYVER="${GO2_PYTHON:-3.12}"

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

OS="$(uname -s)"
case "$OS" in
  Darwin) ;;
  Linux)  ;;
  *) die "this installer supports macOS and Linux; this is $OS" ;;
esac

# ---------------------------------------------------------------- architecture
ARCH="$(uname -m)"
if [ "$OS" = "Darwin" ] && [ "$ARCH" = "x86_64" ]; then
  # A Terminal running under Rosetta reports x86_64 on an Apple Silicon Mac.
  # Installing the Intel stack there would work but be needlessly slow, and
  # would pin an old MuJoCo for no reason.
  if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
    die "This Terminal is running under Rosetta, so it looks like an Intel Mac.
Quit Terminal, right-click Terminal in Finder, choose Get Info, untick
'Open using Rosetta', reopen it and run this again."
  fi
fi

# MuJoCo 4 ships macOS wheels for Apple Silicon only. Intel Macs stay on the
# 3.3 line, which still has x86_64 wheels and everything this project uses.
MUJOCO_SPEC=""
if [ "$OS" = "Darwin" ] && [ "$ARCH" = "x86_64" ]; then
  MUJOCO_SPEC="mujoco>=3.3,<4"
  warn "Intel Mac: pinning MuJoCo 3.x (4.x is Apple Silicon only). Everything works; it is just slower."
fi

say "Go2 simulator installer  ($OS $ARCH, into $PREFIX)"

# ------------------------------------------------------------------------ uv
UV="$(command -v uv || true)"
if [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"; fi
if [ -z "$UV" ]; then
  say "Installing uv (downloads Python for us; no admin password needed)"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null \
    || die "could not install uv. Are you online?"
  UV="$HOME/.local/bin/uv"
fi
[ -x "$UV" ] || die "uv is still not executable at $UV"
say "uv: $("$UV" --version)"

# -------------------------------------------------------------------- python
say "Fetching Python $PYVER (about 30 MB, once)"
"$UV" python install "$PYVER" >/dev/null 2>&1 || true

mkdir -p "$PREFIX/bin"
if [ ! -x "$VENV/bin/python" ]; then
  "$UV" venv --python "$PYVER" "$VENV" >/dev/null || die "could not create the environment at $VENV"
fi

# ------------------------------------------------------------------- package
say "Installing the simulator (about 250 MB the first time)"

# Download the source ourselves rather than handing uv the URL. Re-running
# this installer is the documented way to update, and the version number does
# not change on every push -- so the one thing that must never happen is a
# re-run that quietly installs nothing and leaves the student on old code.
# A fresh temporary path each time sits outside any HTTP or build cache, and
# curl gives a plain "are you online" failure instead of a resolver error.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$TARBALL" -o "$TMP/go2-source.tar.gz" \
  || die "could not download the simulator from ${TARBALL}. Are you online?"

# One resolve, with the MuJoCo pin (if any) in the same command: asking for it
# separately leaves the resolver free to satisfy go2's "mujoco>=3.2" some
# other way on the second pass.
INSTALL_ARGS=("go2[lab] @ ${TMP}/go2-source.tar.gz")
if [ -n "$MUJOCO_SPEC" ]; then
  INSTALL_ARGS+=("$MUJOCO_SPEC")
fi
"$UV" pip install --python "$VENV/bin/python" --upgrade \
     --reinstall-package go2 "${INSTALL_ARGS[@]}" \
  || die "could not install the simulator. The messages above say why."

# ------------------------------------------------------------------ launcher
# A .command file rather than a bare script: double-clicking one opens a
# Terminal window, so the student can read what happened and press Ctrl-C to
# stop. An app that runs silently is impossible to quit and impossible to
# debug, and this code has never run on the machine it is about to run on.
LAUNCH="$PREFIX/bin/go2-lab.command"
cat > "$LAUNCH" <<LAUNCHER
#!/bin/bash
# Written by the Go2 installer. Re-run the installer to update it.
clear
# Name the window now, not after Python has loaded -- the student is looking
# at it during the slow part. go2-lab sets the same title again later.
printf '\033]0;Go2 Lab\007'
echo "Starting the Go2 Lab ... (the first run takes a few seconds)"
echo "Leave this Go2 Lab window open. Closing it stops the lab."
echo
exec "$VENV/bin/go2-lab" "\$@"
LAUNCHER
chmod +x "$LAUNCH"

cat > "$PREFIX/bin/go2-uninstall" <<UNINSTALL
#!/bin/bash
set -e
echo "This removes the Go2 Lab program. Your notebooks are NOT touched."
read -r -p "Continue? [y/N] " a
[ "\$a" = "y" ] || exit 0
rm -rf "$PREFIX"
rm -rf "\$HOME/Applications/Go2 Lab.app"
rm -rf "\$HOME/Applications/Go2 Simulator.app"   # the name used before
echo "Removed. Your notebooks are untouched, in your Documents folder."
UNINSTALL
chmod +x "$PREFIX/bin/go2-uninstall"

# Plain command names for anyone who puts ~/.go2/bin on their PATH. The .app
# does not need these; they are for mentors and for students who end up in a
# terminal anyway.
for tool in go2-lab go2-run; do
  cat > "$PREFIX/bin/$tool" <<WRAPPER
#!/bin/bash
exec "$VENV/bin/$tool" "\$@"
WRAPPER
  chmod +x "$PREFIX/bin/$tool"
done

# ----------------------------------------------------------------- the .app
if [ "$OS" = "Darwin" ]; then
  # Named "Go2 Lab", not "Go2 Simulator", because the notebooks folder is in
  # Spotlight too and a folder the student writes to all day outranks an app
  # they launch once -- so typing the old name offered the folder first, and
  # opening it did nothing. "Go2 Lab" matches the window title and the
  # go2-lab command, and nothing else on the machine answers to it.
  APP="$HOME/Applications/Go2 Lab.app"
  rm -rf "$APP"
  # Retire the old name, but only if it is one of ours: the marker is the
  # launcher path we wrote into it.
  OLDAPP="$HOME/Applications/Go2 Simulator.app"
  if [ -e "$OLDAPP/Contents/MacOS/go2" ] && grep -q "\.go2/bin/go2-lab.command" "$OLDAPP/Contents/MacOS/go2" 2>/dev/null; then
    rm -rf "$OLDAPP"
    say "Replaced the older \"Go2 Simulator\" app with \"Go2 Lab\"."
  fi
  mkdir -p "$APP/Contents/MacOS"
  cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>              <string>Go2 Lab</string>
  <key>CFBundleDisplayName</key>       <string>Go2 Lab</string>
  <key>CFBundleIdentifier</key>        <string>org.cpmentorship.go2</string>
  <key>CFBundleVersion</key>           <string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key>       <string>APPL</string>
  <key>CFBundleExecutable</key>        <string>go2</string>
  <key>LSMinimumSystemVersion</key>    <string>11.0</string>
  <key>LSUIElement</key>               <true/>
</dict>
</plist>
PLIST
  cat > "$APP/Contents/MacOS/go2" <<APPEXEC
#!/bin/bash
# Hand off to Terminal so there is a window to read and a Ctrl-C to press.
open -a Terminal "$LAUNCH"
APPEXEC
  chmod +x "$APP/Contents/MacOS/go2"
  # Belt and braces: this bundle was written locally so it has no quarantine
  # flag, but if the user ever copies it around, clear it anyway.
  xattr -cr "$APP" 2>/dev/null || true
fi

# Where go2-lab will keep the student's notebooks. Said out loud below,
# because "your work is saved somewhere" is not an answer.
if [ "$OS" = "Darwin" ]; then
  if [ -d "$HOME/Documents/Go2 Simulator" ]; then
    NOTEBOOKS="your Documents folder, under \"Go2 Simulator\""
  else
    NOTEBOOKS="your Documents folder, under \"Go2 Notebooks\""
  fi
else
  NOTEBOOKS="~/go2-lab"
fi

# ------------------------------------------------------------------- verify
say "Checking that it actually works"
if ! "$VENV/bin/go2-lab" --check; then
  warn "The self-test above found a problem. The install is in place; send that output to your mentor."
fi

echo
say "Done."
if [ "$OS" = "Darwin" ]; then
  cat <<DONE

  TO START IT

    1. Press Command-Space.
    2. Type:  Go2 Lab
    3. Press Return.

    Pick the result with the APP icon, named "Go2 Lab". Spotlight
    also lists folders; a folder will not start anything.

    It is in your own Applications folder if you would rather click:
    in Finder press Shift-Command-H for your home folder, then open
    "Applications" inside it. That is yours ($HOME/Applications), not
    the system Applications in the sidebar.

    Prefer Terminal? Paste this line and press Return:

        "$LAUNCH"

  The first time, macOS may ask whether Terminal can read your
  Documents folder. Say yes -- that is where your notebooks are kept.

DONE
else
  cat <<DONE

  Start it:   $LAUNCH

DONE
fi
cat <<DONE2
  A window titled "Go2 Lab" opens and stays open -- that is the lab
  running; closing it stops the lab. Your browser opens with the
  notebook on the left and the dog on the right.

  Your notebooks are saved in $NOTEBOOKS. Back that folder up;
  nothing else installed here is yours.

  For the commands (go2-lab, go2-run) on your PATH, add this to
  your shell profile:   export PATH="\$HOME/.go2/bin:\$PATH"

  To update later, run this installer again.
DONE2

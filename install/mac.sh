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
# Escape hatch for a Mac we have not seen: force one MuJoCo version.
GO2_MUJOCO="${GO2_MUJOCO:-}"

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# Ask the person a question and echo their answer.
#
# This script is itself on stdin (curl | bash), so the answer has to come from
# the terminal directly or `read` would eat the rest of the script. Opening
# /dev/tty is the only honest test of whether anyone is there: it can exist,
# and be readable by its permission bits, and still fail to open when there is
# no controlling terminal. Callers wrap this in 2>/dev/null and treat failure
# as "nobody is there", which is why it must never print the prompt to stdout.
ask_tty() {
  local prompt="$1" ans
  exec 3<>/dev/tty || return 1
  printf '%s' "$prompt" >&3
  read -r ans <&3 || ans=""
  exec 3>&-
  printf '%s' "$ans"
}

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

# MuJoCo stopped publishing macOS x86_64 wheels after 3.10.0: 3.11 onward is
# Apple Silicon only on the Mac, and 4.x moved the binary into mujoco-core,
# which is arm64-only too. So an Intel Mac has exactly one ceiling, and it is
# 3.10 -- not "<4", which resolves to 3.17 and has no wheel for this machine.
#
# Getting that wrong is not a slow install, it is a FAILED one: with no wheel,
# pip falls back to building MuJoCo from its sdist, that build wants a
# prebuilt C library via MUJOCO_PATH, and the student gets sixty lines of
# setuptools traceback ending in a variable they have never heard of.
MUJOCO_SPEC=""
if [ -n "$GO2_MUJOCO" ]; then
  MUJOCO_SPEC="$GO2_MUJOCO"
  say "Using the MuJoCo version you asked for: $MUJOCO_SPEC"
elif [ "$OS" = "Darwin" ] && [ "$ARCH" = "x86_64" ]; then
  MUJOCO_SPEC="mujoco>=3.3,<3.11"
  warn "Intel Mac: pinning MuJoCo 3.10 (the last with Intel wheels). Everything works; it is just slower."
fi

say "Go2 simulator installer  ($OS $ARCH, into $PREFIX)"

# ------------------------------------------------- is the lab running now?
# Upgrading underneath a running lab replaces files the running processes are
# still reading. Python keeps what it has already imported, so nothing fails
# at the moment of the install -- it fails twenty minutes later, in a
# notebook, as an ImportError the student cannot possibly connect to this.
#
# Worse, it looks like it worked: the running lab keeps the OLD code in
# memory, so whatever the student came here to fix is still broken.
#
# Note this runs BEFORE the 250 MB download, so stopping here costs nothing.
running_lab() { pgrep -f "$VENV/bin" 2>/dev/null || true; }
# The launcher itself, as opposed to the JupyterLab and simulator it started.
lab_leader() { pgrep -f "$VENV/bin/go2-lab" 2>/dev/null || true; }

PIDS="$(running_lab)"
if [ -n "$PIDS" ]; then
  warn "The Go2 Lab is already running (pid $(echo "$PIDS" | tr '\n' ' '))."
  echo "    Updating underneath it would leave the running lab half old and"
  echo "    half new, and whatever you are updating to fix would still look"
  echo "    broken, because the running copy stays in memory."
  echo
  ANS="${GO2_STOP_RUNNING:-}"
  if [ -z "$ANS" ]; then
    ANS="$(ask_tty '    Stop it and carry on? [y/N] ' 2>/dev/null || true)"
    echo
  fi
  case "$ANS" in
    y|Y|yes|YES|1)
      # Signal the LAUNCHER, and only the launcher. It turns SIGTERM into the
      # same shutdown Ctrl-C does and takes its children with it. Killing the
      # whole set at once instead makes the simulator die first, and the
      # supervisor -- whose job is to survive exactly that -- restarts it
      # before the launcher has processed its own signal.
      LEADER="$(lab_leader)"
      if [ -n "$LEADER" ]; then
        echo "$LEADER" | xargs kill 2>/dev/null || true
      else
        echo "$PIDS" | xargs kill 2>/dev/null || true
      fi

      # Gone means gone twice over: a restart in flight can leave a gap where
      # nothing matches for an instant. Waiting for two consecutive clear
      # polls costs a second and is the difference between installing over a
      # stopped lab and installing over a reviving one.
      CLEAR=0
      for _ in $(seq 1 30); do
        if [ -z "$(running_lab)" ]; then
          CLEAR=$((CLEAR + 1))
          [ "$CLEAR" -ge 2 ] && break
        else
          CLEAR=0
        fi
        sleep 1
      done

      LEFT="$(running_lab)"
      if [ -n "$LEFT" ]; then
        warn "Something is still running; stopping it the hard way."
        echo "$LEFT" | xargs kill -9 2>/dev/null || true
        sleep 2
        [ -z "$(running_lab)" ] || die "Could not stop the lab (pid $(running_lab | tr '\n' ' ')).
Close the Go2 Lab window yourself, then run this again."
      fi
      say "Stopped the running lab. Carrying on."
      ;;
    *)
      die "Close the Go2 Lab window (or press Ctrl-C in it), then run this again.
Nothing has been changed."
      ;;
  esac
fi

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

# Never build MuJoCo from source. There is no supported way to do it on a
# student laptop -- it needs a prebuilt C library pointed at by MUJOCO_PATH --
# so a source fallback is never the right answer, it is just a confusing way
# to fail. Refusing it makes the resolver step BACK to a version that does
# ship a wheel for this machine, which is the behaviour we actually want and
# keeps working when the next release drops a platform. go2 itself is a
# source tarball, so this is named per package rather than :all:.
NO_SOURCE_BUILDS=(--only-binary mujoco --only-binary mujoco-core
                  --only-binary mujoco-studio)

INSTALL_LOG="$TMP/install.log"
if ! "$UV" pip install --python "$VENV/bin/python" --upgrade \
       --reinstall-package go2 "${NO_SOURCE_BUILDS[@]}" "${INSTALL_ARGS[@]}" \
       2>&1 | tee "$INSTALL_LOG"; then
  # Translate the one failure we know is unreadable. Everything else still
  # gets "the messages above say why", which is honest.
  if grep -q 'MUJOCO_PATH\|Failed to build .mujoco' "$INSTALL_LOG" 2>/dev/null; then
    die "MuJoCo has no ready-made package for this Mac ($ARCH), so the installer
tried to build it from source, which cannot work on a laptop.

This is our bug, not anything you did. Please send your mentor:

    the line above that starts with 'Failed to build'
    this Mac's chip (Apple menu > About This Mac)

As a workaround, an older MuJoCo usually has a package for this machine:

    GO2_MUJOCO='mujoco==3.10.0' bash mac.sh"
  fi
  die "could not install the simulator. The messages above say why."
fi

# --------------------------------------------------------------- build stamp
# Nothing else records which code a student is running: the package is built
# on their machine from a branch tarball, so its version number is the same
# string for every build ever made. The timestamp orders builds and the hash
# of the exact tarball identifies the code.
STAMP="$(date -u +%Y%m%d-%H%M%S)Z"
if command -v shasum >/dev/null 2>&1; then
  SRC_SHA="$(shasum -a 256 "$TMP/go2-source.tar.gz" | cut -c1-12)"
elif command -v sha256sum >/dev/null 2>&1; then
  SRC_SHA="$(sha256sum "$TMP/go2-source.tar.gz" | cut -c1-12)"
else
  SRC_SHA=""
fi
# -I, or this resolves `go2` from the current directory: run the installer
# from a clone and the stamp lands in the clone instead of the install.
SITE="$("$VENV/bin/python" -I -c 'import go2, pathlib; print(pathlib.Path(go2.__file__).parent)')"
[ -d "$SITE" ] || die "could not find the installed package to stamp"
cat > "$SITE/_build.py" <<BUILDPY
# Written by the installer at install time. Not in git, not in the tarball.
BUILD = "$STAMP"
SOURCE = "$TARBALL"
SOURCE_SHA = "$SRC_SHA"
BUILDPY
say "Build $STAMP${SRC_SHA:+ ($SRC_SHA)}"

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
  # shellcheck disable=SC2088  # prose for a human, not a path we open: the
  # other branches are whole English sentences, and "~/go2-lab" is friendlier
  # to read than an expanded /home/<name>/go2-lab.
  NOTEBOOKS="~/go2-lab"
fi

# ------------------------------------------------------------------- verify
say "Checking that it actually works"
CHECK_OK=1
if ! "$VENV/bin/go2-lab" --check; then
  CHECK_OK=0
  warn "The self-test above found a problem. The install is in place; send that output to your mentor."
fi

# ------------------------------------------------------------- start it now
# Finish by opening the lab. "Now go and find an app you have never seen" is
# exactly the step a student loses, and this is also what makes an upgrade
# seamless: if we stopped a running lab above, this puts it back.
#
# Only when the self-test passed -- opening a broken lab teaches nothing and
# buries the error above it -- and only when there is a terminal to ask, so a
# scripted or class-wide install never has windows appear on someone's screen.
START_NOW=""
if [ "$OS" = "Darwin" ] && [ "$CHECK_OK" = "1" ] && [ -z "${GO2_NO_LAUNCH:-}" ]; then
  case "${GO2_START_NOW:-}" in
    y|Y|yes|YES|1) START_NOW=1 ;;
    *)
      # The exit status, not the answer, is what says whether anyone was
      # there: an empty answer means Return (yes), a failure means no
      # terminal, and those must not take the same branch.
      if ANSWER="$(ask_tty 'Start the Go2 Lab now? [Y/n] ' 2>/dev/null)"; then
        case "$ANSWER" in
          n|N|no|NO) ;;
          *) START_NOW=1 ;;
        esac
        echo
      fi
      ;;
  esac
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

# The instructions above stay on screen in this window; the lab opens in its
# own. Last, so that a student who says no still has the whole message.
if [ -n "$START_NOW" ]; then
  say "Opening the Go2 Lab in a new window ..."
  open -a Terminal "$LAUNCH"
fi

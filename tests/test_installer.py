"""Checks on install/mac.sh that shellcheck does not make.

Nothing in CI runs this script: it downloads 250 MB, writes to a home
directory and installs an app, so the test suite reads it instead. That
leaves one gap worth closing by hand -- shellcheck does not know which
commands exist, so a helper that is deleted while its calls remain is
reported by nobody. It becomes `warn: command not found`, at the moment a
student is being told something important, on their machine, not ours.

That is not hypothetical; it is why this file exists.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "install" / "mac.sh"

# The script's own helpers, written down rather than discovered. Inferring
# them from the definitions would make the test agree with whatever the file
# currently says, including "this helper no longer exists" -- which is the
# one thing it is here to catch.
HELPERS = {"say", "warn", "die", "ask_tty", "local_time",
           "running_lab", "lab_leader"}


def _text() -> str:
    return SCRIPT.read_text()


def _defined(text: str) -> set[str]:
    return set(re.findall(r"^([a-z_][a-z0-9_]*)\(\)", text, re.M))


def test_every_helper_the_installer_uses_is_defined():
    defined = _defined(_text())
    missing = sorted(HELPERS - defined)
    assert not missing, (
        f"install/mac.sh calls {missing} but no longer defines them; "
        "a student would see 'command not found' instead")


def test_no_helper_is_defined_and_then_never_called():
    """Dead helpers are how the set above drifts out of date."""
    text = _text()
    for name in sorted(_defined(text)):
        body_removed = re.sub(rf"^{name}\(\).*?^}}", "", text, flags=re.M | re.S)
        uses = re.findall(rf"(?<![\w-]){name}(?![\w(-])", body_removed)
        assert uses, f"{name}() is defined in install/mac.sh and never called"


def test_the_installer_parses():
    """bash -n catches what a reviewer's eye does not."""
    out = subprocess.run(["bash", "-n", str(SCRIPT)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_the_release_line_comes_before_anything_is_changed():
    """Its whole point is being readable while there is still time to stop."""
    text = _text()
    release = text.index('say "Release:')
    for later in ('say "Installing the simulator',
                  'The Go2 Lab is already running',
                  'venv --python'):
        assert release < text.index(later), f"the release line is after: {later}"

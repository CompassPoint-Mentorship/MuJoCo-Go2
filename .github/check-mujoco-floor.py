#!/usr/bin/env python3
"""Fail if the resolved MuJoCo is older than the version we have tested on.

    check-mujoco-floor.py resolved.txt 3.10.0

Why this exists, and why a successful resolve is not enough:

The installer passes --only-binary for the mujoco packages, so pip is forbidden
from building MuJoCo from source. That is deliberate -- a source build needs a
prebuilt C library via MUJOCO_PATH and cannot work on a student laptop -- but
it has a quiet consequence. When a release stops shipping a wheel for some
platform, the resolver does not fail. It steps BACK to an older release that
still has one, and says nothing.

That is the behaviour we want on the day it happens, and a slow leak after
that: nobody files a bug, because nothing looks broken, and students drift
backwards release by release until they land somewhere our code does not work
-- which surfaces as a strange error in a notebook, long after the cause.

So the check is not "did it resolve", it is "did it resolve to something we
have actually run the test suite against".
"""

from __future__ import annotations

import pathlib
import re
import sys


def version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    path, floor = pathlib.Path(sys.argv[1]), sys.argv[2]

    found = re.search(r"^mujoco==([0-9][0-9.]*)", path.read_text(), re.M)
    if not found:
        print(f"FAIL: no mujoco in {path} -- did the resolve actually run?",
              file=sys.stderr)
        return 1

    got = found.group(1)
    if version(got) < version(floor):
        print(
            f"FAIL: resolves to mujoco {got}, below the tested floor {floor}.\n\n"
            "Nothing is broken yet, which is the point of catching it here.\n"
            "MuJoCo has most likely stopped publishing a wheel for this\n"
            "platform, so the resolver walked backwards to one that has it.\n\n"
            "Decide which it is:\n"
            "  * the new floor is fine  -> test on it and raise the floor;\n"
            "  * the drop is a problem  -> pin, or say out loud in the README\n"
            "    that the platform is no longer supported.\n\n"
            "What you must not do is leave students resolving to a version\n"
            "nobody has run the suite against.",
            file=sys.stderr,
        )
        return 1

    print(f"ok: mujoco {got} (floor {floor})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

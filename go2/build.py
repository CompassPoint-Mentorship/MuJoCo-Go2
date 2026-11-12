"""Which build this is.

There is no build server: `go2-lab` is built on the student's own machine,
from a tarball of whatever `main` held at that moment. So the question
"which version are you running?" has no answer in the source tree -- and it
is the first question worth asking when a student reports something odd.

The installer answers it by writing `_build.py` into the installed package:
a timestamp to the second, and a short hash of the exact tarball it built
from. The timestamp orders builds; the hash identifies the code, so two
students who installed minutes apart from the same commit carry the same
hash, and one who installed before a fix does not.

A source checkout has no `_build.py`, and says so rather than inventing one.
"""

from __future__ import annotations

UNKNOWN = "source checkout"


def build_info() -> dict[str, str]:
    """Build stamp, source URL and source hash; empty strings when unknown."""
    try:
        from . import _build  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - any failure means "not installed"
        return {"build": UNKNOWN, "source": "", "sha": ""}
    return {
        "build": str(getattr(_build, "BUILD", UNKNOWN)),
        "source": str(getattr(_build, "SOURCE", "")),
        "sha": str(getattr(_build, "SOURCE_SHA", "")),
    }


def build_id() -> str:
    """One line for a banner, a panel footer or a bug report."""
    info = build_info()
    return f"{info['build']} ({info['sha']})" if info["sha"] else info["build"]

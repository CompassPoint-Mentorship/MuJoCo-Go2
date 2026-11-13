"""Which build this is.

There is no build server: `go2-lab` is built on the student's own machine,
from a tarball of whatever `main` held at that moment. So the question
"which version are you running?" has no answer in the source tree -- and it
is the first question worth asking when a student reports something odd.

The installer answers it by writing `_build.py` into the installed package:
a timestamp to the second, the commit it resolved the branch to, and a short
hash of the exact tarball it built from. The timestamp orders builds; the
commit names the code in terms anyone can look up; the hash identifies it
even when there was no commit to be had, so two students who installed
minutes apart from the same source carry the same hash, and one who
installed before a fix does not.

A source checkout has no `_build.py`, and says so rather than inventing one.
"""

from __future__ import annotations

UNKNOWN = "source checkout"


def build_info() -> dict[str, str]:
    """Build stamp, source URL and source hash; empty strings when unknown."""
    try:
        from . import _build  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - any failure means "not installed"
        return {"build": UNKNOWN, "source": "", "sha": "", "commit": ""}
    return {
        "build": str(getattr(_build, "BUILD", UNKNOWN)),
        "source": str(getattr(_build, "SOURCE", "")),
        "sha": str(getattr(_build, "SOURCE_SHA", "")),
        "commit": str(getattr(_build, "COMMIT", "")),
    }


def build_id() -> str:
    """One line for a banner, a panel footer or a bug report.

    The commit is the better answer when the installer could get one: it says
    which code this is in terms anyone can look up, while the tarball hash
    only says "the same as that other student's". Older stamps have no
    commit, so the hash remains the fallback.
    """
    info = build_info()
    if info["commit"]:
        return f"{info['build']} ({info['commit'][:7]})"
    return f"{info['build']} ({info['sha']})" if info["sha"] else info["build"]

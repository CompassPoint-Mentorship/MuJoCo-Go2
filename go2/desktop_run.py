"""Entry point for the second half of `go2-run` on macOS.

`go2-run` re-execs itself as `mjpython -m go2.desktop_run <script>`, because
mjpython takes a module or a file, not a console-script name. Keeping this in
its own module means the re-exec target is unambiguous.
"""

from __future__ import annotations

import sys

from .desktop import run_script

if __name__ == "__main__":
    raise SystemExit(run_script(sys.argv[1:]))

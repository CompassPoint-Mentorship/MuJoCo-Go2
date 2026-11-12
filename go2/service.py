"""Run the simulator as a long-lived service.

    python -m go2.service --port 8399

One process owns the dog. Notebooks connect to it with RemoteBackend (set
GO2_SIM_URL), and the viewer shows that same dog -- so the view is live before
anyone has run a cell, and restarting a kernel does not destroy the world.

Endpoints:
    /            drivable viewer page
    /split       JupyterLab on the left, the viewer on the right
    /frame       one JPEG of the chase camera (what the viewer polls)
    /camera      one JPEG of any camera, with width/height
    /state       pose and commanded velocity, as JSON
    /cmd         move/stop/stand/sit
    /stream      MJPEG; direct use only, proxies buffer it
"""

from __future__ import annotations

import argparse
import os

from .web import serve


def main() -> None:
    # This process IS the simulator, so it must own a real one. The hosted
    # image sets GO2_SIM_URL for the notebook kernels; inherited here it would
    # make the service try to connect to itself and fail at startup.
    os.environ.pop("GO2_SIM_URL", None)

    ap = argparse.ArgumentParser(description="Go2 simulator service")
    ap.add_argument("--host", default="127.0.0.1",
                    help="default 127.0.0.1: reached through jupyter-server-proxy")
    ap.add_argument("--port", type=int, default=8399)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--camera", default="chase")
    ap.add_argument("--scene", default=None)
    serve(**vars(ap.parse_args()))


if __name__ == "__main__":
    main()

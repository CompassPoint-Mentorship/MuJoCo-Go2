"""Real Go2 Pro backend -- NOT IMPLEMENTED YET (step 2).

This file exists so the seam is visible: Go2(sim=False) is a real code path
that raises a clear error, not a hole in the design.

WHAT THIS WILL DO
-----------------
Talk to a physical Go2 Pro over WebRTC -- the same channel the Unitree phone
app uses. The Pro does NOT support unitree_sdk2 / CycloneDDS low-level control
(that is an EDU feature), so WebRTC is the supported route for high-level
commands, and it is all we need: Unitree's own walking controller lives on the
robot and does the walking for us.

Planned implementation: wrap `unitree_webrtc_connect` (or `go2_webrtc_connect`)
and map this module's methods onto its sport-mode commands.

KNOWN GOTCHAS, to handle when building this
-------------------------------------------
1. The Unitree phone app must be disconnected first -- the robot accepts one
   WebRTC client at a time.
2. Firmware >= 1.1.15 requires a per-device AES-128 key, obtained with a CLI
   tool bundled with the driver. Older firmware needs no extra auth.
3. These drivers are community projects, not official Unitree software. A
   firmware update can break them. Keep this file thin so a fix stays cheap.
4. The Pro has no onboard Jetson, so code is NOT uploaded to the dog: it runs
   on the student's laptop and drives the robot over WiFi.

Unlike the simulator, `move()` here is a *request*. The real controller has
tracking error, the dog can be blocked or can slip, and pose() will come from
the robot's own odometry, which drifts. Student code should already tolerate
that, because it never assumed otherwise.
"""

from __future__ import annotations

import numpy as np

from .base import Backend, Pose, Velocity

_MESSAGE = (
    "The real-robot backend is not implemented yet (that is step 2 of the build).\n"
    "Use Go2(sim=True) for now.\n\n"
    "See go2/backends/real.py for the plan and the known gotchas."
)


class RealBackend(Backend):
    def __init__(self, ip: str | None = None, serial: str | None = None) -> None:
        raise NotImplementedError(_MESSAGE)

    # The abstract methods below are never reached; they exist so this class is
    # instantiable-shaped and the interface stays honest.
    def say(self, data: bytes, content_type: str = "audio/webm") -> str:
        # Step 2: POST the clip to the robot's audio service. go2-pocket
        # drives this over WebRTC on rt/api/audiohub/request, and the Go2
        # Pro has a speaker, so this needs no EDU hardware.
        raise NotImplementedError(_MESSAGE)
    def listen(self, seconds: float = 3.0) -> str | None:
        # Step 2: the Go2 has microphones of its own, so unlike the simulator
        # this records on the robot rather than in the browser.
        raise NotImplementedError(_MESSAGE)
    def backflip(self) -> None:
        # Step 2: sport API id 2043 on rt/api/sport/request. This one needs
        # more than a mapping -- clear space, a flat floor, and a human with
        # the remote. See docs/STEP2-REAL-ROBOT.md.
        raise NotImplementedError(_MESSAGE)
    def reset(self) -> None: raise NotImplementedError(_MESSAGE)
    def stand(self) -> None: raise NotImplementedError(_MESSAGE)
    def sit(self) -> None: raise NotImplementedError(_MESSAGE)
    def move(self, vx: float, vy: float, wz: float) -> None: raise NotImplementedError(_MESSAGE)
    def stop(self) -> None: raise NotImplementedError(_MESSAGE)
    def pose(self) -> Pose: raise NotImplementedError(_MESSAGE)
    def velocity(self) -> Velocity: raise NotImplementedError(_MESSAGE)
    def camera(self, width: int, height: int, camera: str = "head") -> np.ndarray: raise NotImplementedError(_MESSAGE)
    def close(self) -> None: raise NotImplementedError(_MESSAGE)

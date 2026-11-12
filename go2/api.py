"""The class students use.

    from go2 import Go2

    dog = Go2(sim=True)      # simulator on this laptop
    # dog = Go2(sim=False)   # the real robot over WiFi  (step 2)

    dog.stand()
    dog.move(vx=0.4)
    time.sleep(3)
    dog.stop()

Everything below the Go2 class is a backend detail. Student code should not
import from go2.backends.
"""

from __future__ import annotations

import pathlib
import time

import numpy as np

from .backends.base import Backend, Pose, Velocity

# Commands are clamped to these before reaching any backend, so a typo in an
# assignment cannot ask the real dog for something alarming.
MAX_VX = 1.0    # m/s forward / backward
MAX_VY = 0.6    # m/s sideways
MAX_WZ = 1.5    # rad/s turning


class Go2:
    """One dog: simulated or real, same methods either way."""

    def __init__(
        self,
        sim: bool = True,
        viewer: bool = True,
        scene: str | pathlib.Path | None = None,
        realtime: bool = True,
        ip: str | None = None,
    ) -> None:
        """
        Args:
            sim:      True for the simulator, False for the physical robot.
            viewer:   Open a window. Simulator only. Set False for headless runs.
            scene:    Path to an alternative MJCF arena. Simulator only.
            realtime: Run the simulation at wall-clock speed. Set False to let
                      it run as fast as the CPU allows (useful for tests).
            ip:       Robot address. Real robot only.
        """
        self.sim = bool(sim)
        if self.sim:
            from .backends.sim import SimBackend

            self._backend: Backend = SimBackend(scene=scene, viewer=viewer, realtime=realtime)
        else:
            from .backends.real import RealBackend

            self._backend = RealBackend(ip=ip)

    # ---------------------------------------------------------------- motion

    def stand(self) -> "Go2":
        """Stand still on all four legs."""
        self._backend.stand()
        return self

    def sit(self) -> "Go2":
        """Lower the body to the ground."""
        self._backend.sit()
        return self

    def move(self, vx: float = 0.0, vy: float = 0.0, wz: float = 0.0) -> "Go2":
        """Walk at a body-frame velocity until told otherwise.

        Args:
            vx: forward (+) / backward (-), m/s
            vy: left (+) / right (-), m/s
            wz: turn counter-clockwise (+) / clockwise (-), rad/s

        The command persists -- call stop() to halt. This matches the real
        robot, which keeps walking until the next command arrives.
        """
        self._backend.move(
            _clamp(vx, MAX_VX), _clamp(vy, MAX_VY), _clamp(wz, MAX_WZ)
        )
        return self

    def forward(self, speed: float = 0.4) -> "Go2":
        """Shorthand for move(vx=speed)."""
        return self.move(vx=speed)

    def turn(self, rate: float = 0.8) -> "Go2":
        """Shorthand for move(wz=rate). Positive is counter-clockwise."""
        return self.move(wz=rate)

    def stop(self) -> "Go2":
        """Stop moving. The dog stays standing."""
        self._backend.stop()
        return self

    # --------------------------------------------------------------- sensing

    def pose(self) -> Pose:
        """Where the dog is: Pose(x, y, yaw) in the world frame."""
        return self._backend.pose()

    def velocity(self) -> Velocity:
        """The velocity currently being commanded."""
        return self._backend.velocity()

    def camera(self, width: int = 640, height: int = 480) -> np.ndarray:
        """A forward-facing RGB image as a (height, width, 3) uint8 array."""
        return self._backend.camera(width=width, height=height)

    # ------------------------------------------------------------- lifecycle

    def wait(self, seconds: float) -> "Go2":
        """Sleep while the dog keeps doing whatever it was told to do.

        Identical to time.sleep(); provided so beginner scripts read as one
        continuous instruction list.
        """
        time.sleep(seconds)
        return self

    def close(self) -> None:
        """Shut down the simulator window or disconnect from the robot."""
        self._backend.close()

    def __enter__(self) -> "Go2":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, float(value)))

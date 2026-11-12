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

import os
import pathlib
import time

import numpy as np

from .backends.base import Backend, Pose, Velocity

# Real-robot mode is OFF unless the environment explicitly turns it on.
#
# Why: a hosted notebook can be opened from anywhere, and Go2(sim=False) drives
# a real 15 kg robot that may have nobody standing next to it. Simulation is
# always safe; hardware is not. So hardware is opt-in, per deployment, by
# someone who knows a human is present with the remote in hand.
#
#   export GO2_ALLOW_REAL=1      # on a machine where that is true
#
# Shared teaching servers simply never set it.
_ALLOW_REAL_ENV = "GO2_ALLOW_REAL"

# When set, Go2(sim=True) connects to a simulator service instead of starting
# one in this process. The hosted containers set it; a local install does not.
_SIM_URL_ENV = "GO2_SIM_URL"

_REAL_DISABLED = """Real-robot mode is disabled on this machine.

Go2(sim=False) drives a physical robot. It is switched off here because this
looks like a shared or hosted environment, where code can be run by someone
who is not in the room with the dog.

To work on the simulator instead:

    dog = Go2(sim=True)

If you are physically with the robot, have the remote in hand as an emergency
stop, and the slot is booked, an operator can enable it with:

    export {env}=1
"""

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
        url: str | None = None,
    ) -> None:
        """
        Args:
            sim:      True for the simulator, False for the physical robot.
            viewer:   Open a window. Simulator only. Set False for headless runs.
            scene:    Path to an alternative MJCF arena. Simulator only.
            realtime: Run the simulation at wall-clock speed. Set False to let
                      it run as fast as the CPU allows (useful for tests).
            ip:       Robot address. Real robot only.
            url:      Address of a simulator service to connect to instead of
                      starting one here. Defaults to $GO2_SIM_URL. Simulator
                      only; `viewer`, `scene` and `realtime` are then set by
                      whoever started the service.
        """
        self.sim = bool(sim)
        if not self.sim and os.environ.get(_ALLOW_REAL_ENV, "") not in ("1", "true", "yes"):
            raise PermissionError(_REAL_DISABLED.format(env=_ALLOW_REAL_ENV))
        if self.sim:
            # In hosted mode a long-lived service owns the dog and we are a
            # client of it, so the viewer shows a live dog before any cell has
            # run and a kernel restart does not destroy the world. Locally,
            # GO2_SIM_URL is unset and the simulator runs in this process.
            # `url=""` explicitly forces a local simulator, ignoring the
            # environment -- the service process itself needs that.
            service = url if url is not None else os.environ.get(_SIM_URL_ENV, "").strip()
            if service:
                from .backends.remote import RemoteBackend

                self._backend: Backend = RemoteBackend(service)
                self.remote = True
            else:
                from .backends.sim import SimBackend

                self._backend = SimBackend(scene=scene, viewer=viewer, realtime=realtime)
                self.remote = False
        else:
            from .backends.real import RealBackend

            self._backend = RealBackend(ip=ip)

    # ---------------------------------------------------------------- motion

    def reset(self) -> "Go2":
        """Put the dog back at the start: origin, stopped, standing.

        Worth knowing in hosted mode: the dog lives in a service, not in your
        notebook, so it keeps whatever position and command it had when your
        last cell ended -- even across a kernel restart. Start an experiment
        with reset() and it begins from a known state every time.
        """
        self._backend.reset()
        return self

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

    def camera(self, width: int = 640, height: int = 480, camera: str = "head") -> np.ndarray:
        """An RGB image as a (height, width, 3) uint8 array.

        `camera="head"` is the robot's forward view and is the only one the
        real robot has. The simulator arena also offers `camera="chase"`, an
        outside view that follows the dog -- handy for watching, useless as
        a sensor, and unavailable on hardware.
        """
        return self._backend.camera(width=width, height=height, camera=camera)

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

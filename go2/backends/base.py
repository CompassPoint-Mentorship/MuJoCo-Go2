"""The contract every backend implements.

Student code only ever touches go2.Go2, which forwards to one of these. Adding
a new way to drive a dog -- the real robot, a different simulator, a recording
-- means implementing this interface and nothing else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Pose:
    """Where the dog is, in the world frame. Angles in radians, metres for x/y."""

    x: float
    y: float
    yaw: float

    def __repr__(self) -> str:  # friendlier than the dataclass default
        return f"Pose(x={self.x:.3f}, y={self.y:.3f}, yaw={self.yaw:.3f})"


@dataclass(frozen=True)
class Velocity:
    """Body-frame velocity: vx forward, vy left, wz counter-clockwise."""

    vx: float
    vy: float
    wz: float

    def __repr__(self) -> str:
        return f"Velocity(vx={self.vx:.3f}, vy={self.vy:.3f}, wz={self.wz:.3f})"


class Backend(ABC):
    """One way of driving a Go2."""

    @abstractmethod
    def reset(self) -> None:
        """Put the dog back where it started, stopped and standing."""

    @abstractmethod
    def stand(self) -> None: ...

    @abstractmethod
    def sit(self) -> None: ...

    @abstractmethod
    def move(self, vx: float, vy: float, wz: float) -> None:
        """Command a body-frame velocity. Persists until changed."""

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def pose(self) -> Pose: ...

    @abstractmethod
    def velocity(self) -> Velocity: ...

    @abstractmethod
    def camera(self, width: int, height: int, camera: str = "head") -> np.ndarray:
        """RGB image, shape (height, width, 3), dtype uint8.

        `camera` names a camera in the scene. "head" is the robot's
        forward-facing view; the simulator arena also defines "chase".
        A real robot only has "head".
        """

    @abstractmethod
    def close(self) -> None: ...

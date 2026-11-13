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

    def say_again(self) -> str | None:
        """Play the most recent clip again. Returns its id, or None.

        Not abstract: a backend that cannot replay should say so by
        returning None rather than forcing every backend to implement it.
        """
        return None

    @abstractmethod
    def say(self, data: bytes, content_type: str = "audio/webm") -> str:
        """Play an audio clip from the dog. Returns the clip id.

        Simulated: the browser watching the dog plays it, since MuJoCo has no
        speaker. Real robot: the Go2's own speaker, over its audio service.
        """

    def hush(self) -> None:
        """Stop the clip currently playing. The clip itself is kept.

        Not abstract for the same reason as listen(): a backend with no way
        to interrupt a sound should say so rather than pretend it worked.
        """
        raise NotImplementedError("this dog cannot stop a sound once it starts")

    def listen(self, seconds: float = 3.0) -> str | None:
        """Record from a microphone and keep the clip. Returns its id.

        Not abstract for the same reason as backflip(): a backend with no
        ears should say so, not force every backend to grow them.
        """
        raise NotImplementedError("this dog cannot listen")

    def backflip(self) -> None:
        """One backwards somersault.

        Not abstract: a backend that cannot flip should say so clearly rather
        than every backend having to implement a trick.
        """
        raise NotImplementedError("this dog cannot do a backflip")

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

"""Talk to a simulator running in another process.

Why this exists: when the dog lives inside the notebook kernel, restarting the
kernel destroys it, and nothing can show the dog until a cell has run. So in
hosted mode one long-lived service owns the simulation and every notebook is a
client of it.

    GO2_SIM_URL=http://127.0.0.1:8399  jupyter lab

`Go2(sim=True)` then picks this backend automatically. Student code is
unchanged -- same move(), pose(), camera().

Consequences worth knowing:

  * The dog keeps walking if your cell raises or you restart the kernel.
    Call dog.stop() (or press stop in the viewer) if it runs away.
  * Two notebooks pointed at the same service drive the SAME dog. That is
    deliberate -- it is what lets the viewer show what your code is doing.
  * pose() costs an HTTP round trip (sub-millisecond on localhost, but not
    free). Tight control loops should sleep rather than spin.
"""

from __future__ import annotations

import io
import json
import urllib.parse
import urllib.request

import numpy as np

from .base import Backend, Pose, Velocity

_TIMEOUT = 10.0


class RemoteBackend(Backend):
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        try:
            self._get("state")
        except Exception as exc:  # noqa: BLE001 - want the friendly message
            raise ConnectionError(
                f"No simulator service at {self.url}.\n\n"
                "In hosted mode one service owns the dog and notebooks connect "
                "to it. If you are running locally, unset GO2_SIM_URL to get a "
                "simulator inside this process instead:\n\n"
                "    import os; os.environ.pop('GO2_SIM_URL', None)\n"
            ) from exc

    # ----------------------------------------------------------- transport

    def _get(self, path: str, _timeout: float | None = None, **params) -> bytes:
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        with urllib.request.urlopen(f"{self.url}/{path}{query}",
                                    timeout=_timeout or _TIMEOUT) as r:
            return r.read()

    def _json(self, path: str, **params) -> dict:
        return json.loads(self._get(path, **params).decode())

    # ------------------------------------------------------------- motion

    def say_again(self) -> str | None:
        clip = self._json("audio/state").get("clip")
        if clip:
            self._get("cmd", say=clip)
        return clip

    def hush(self) -> None:
        self._get("cmd", hush=1)

    def say(self, data: bytes, content_type: str = "audio/webm") -> str:
        req = urllib.request.Request(
            f"{self.url}/audio", data=data, method="POST",
            headers={"Content-Type": content_type},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            clip_id = json.loads(r.read().decode())["clip"]
        self._get("cmd", say=clip_id)
        return clip_id

    def reset(self) -> None:
        self._get("cmd", reset=1)

    def listen(self, seconds: float = 3.0) -> str | None:
        # The service waits for the browser to record, so this request is
        # open for as long as the recording takes. Its own timeout has to
        # outlast that or the clip would arrive after we gave up on it.
        raw = self._get("cmd", listen=seconds, _timeout=seconds + 30.0)
        return json.loads(raw.decode()).get("clip")

    def backflip(self) -> None:
        # The service runs the animation and only answers once it has landed,
        # so this blocks for about as long as the flip takes.
        self._get("cmd", backflip=1)

    def stand(self) -> None:
        self._get("cmd", stand=1)

    def sit(self) -> None:
        self._get("cmd", sit=1)

    def move(self, vx: float, vy: float, wz: float) -> None:
        self._get("cmd", vx=vx, vy=vy, wz=wz)

    def stop(self) -> None:
        self._get("cmd", stop=1)

    # ------------------------------------------------------------ sensing

    def pose(self) -> Pose:
        d = self._json("state")
        return Pose(d["x"], d["y"], d["yaw"])

    def velocity(self) -> Velocity:
        d = self._json("state")
        return Velocity(d["vx"], d["vy"], d["wz"])

    def camera(self, width: int = 640, height: int = 480, camera: str = "head") -> np.ndarray:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover
            raise ImportError("camera() needs Pillow:  pip install pillow") from exc
        jpeg = self._get("camera", camera=camera, width=width, height=height)
        return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))

    # ---------------------------------------------------------- lifecycle

    def close(self) -> None:
        # The service outlives this client on purpose, so closing only stops
        # the dog -- it does not shut the simulation down.
        try:
            self.stop()
        except Exception:  # noqa: BLE001 - closing must not raise
            pass

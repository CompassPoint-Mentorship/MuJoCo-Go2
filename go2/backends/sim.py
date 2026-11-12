"""MuJoCo simulator backend.

Runs on macOS and Ubuntu, CPU only, no GPU required.

HOW THE DOG WALKS HERE
----------------------
It doesn't, physically. MuJoCo gives us an accurate Go2 body; Unitree's walking
controller is firmware on the real robot and does not exist in any simulator.
So this backend *drives the base kinematically*: it integrates the commanded
velocity into the robot's world pose and plays a trot animation on the 12 leg
joints (see go2.gait).

Consequences, stated plainly so nobody is misled:

  * The dog cannot fall over, trip, or slip.
  * Commanded velocity is tracked exactly -- there is no tracking error.
  * Obstacles still block it: we run MuJoCo's collision detection each tick and
    refuse motion that would push the body into an obstacle.

That is enough for navigation, perception and behaviour work, which is what
this course is for. Swapping in a trained walking policy later changes this
file only; student code is unaffected.
"""

from __future__ import annotations

import math
import pathlib
import platform
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .. import gait
from ..audio import AudioStore
from .base import Backend, Pose, Velocity

_ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets"
DEFAULT_SCENE = _ASSETS / "arena.xml"

_SIM_DT = 0.005          # 200 Hz control/animation tick
_VIEWER_DT = 0.02        # 50 fps window refresh
_STAND_HEIGHT = 0.27     # metres, matches the model's "home" keyframe
_OBSTACLE_PREFIX = "obs_"

_MACOS_HINT = (
    "\nOn macOS the MuJoCo viewer must own the main thread, so scripts that open\n"
    "a window have to be started with `mjpython` instead of `python`:\n\n"
    "    mjpython your_script.py\n\n"
    "`mjpython` is installed alongside mujoco -- you already have it.\n"
    "To run with no window at all, use Go2(viewer=False).\n"
)


class SimBackend(Backend):
    def __init__(
        self,
        scene: str | pathlib.Path | None = None,
        viewer: bool = True,
        realtime: bool = True,
    ) -> None:
        try:
            import mujoco
        except ImportError as exc:  # pragma: no cover - import guard
            raise ImportError(
                "MuJoCo is not installed. From the project root run:\n"
                "    pip install -e .\n"
            ) from exc

        self._mujoco = mujoco
        self._scene_path = pathlib.Path(scene) if scene else DEFAULT_SCENE
        if not self._scene_path.exists():
            raise FileNotFoundError(f"scene file not found: {self._scene_path}")

        self._model = mujoco.MjModel.from_xml_path(str(self._scene_path))
        self._data = mujoco.MjData(self._model)

        self._joint_qpos0 = 7  # qpos[0:3] xyz, qpos[3:7] quaternion, then 12 joints
        self._obstacle_geoms = self._find_obstacle_geoms()

        # ONE lock guards every access to MjModel/MjData, not just the
        # commanded state. MuJoCo's data is plain memory with no internal
        # locking: stepping it on the sim thread while another thread calls
        # mj_forward (reset) or mjv_updateScene (render) is a data race, and
        # it segfaults the whole process rather than raising. Re-entrant
        # because the tick path legitimately re-acquires it.
        self._lock = threading.RLock()
        self._reset_requested = False
        self._cmd = (0.0, 0.0, 0.0)
        self._mode = "stand"
        self._x = 0.0
        self._y = 0.0
        self._yaw = 0.0
        self._clock = 0.0

        # Clips the dog can say. Held here rather than in the web layer so a
        # local install behaves the same -- though with no viewer open there
        # is nothing to play it, which the docs say plainly.
        self.audio = AudioStore()
        self._renderer = None
        # An EGL context belongs to ONE thread: calling eglMakeCurrent from a
        # second thread fails with EGL_BAD_ACCESS. The notebook's show() runs
        # on the kernel thread while the web viewer renders on its own, so all
        # rendering is funnelled through a single worker that owns the context.
        self._render_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="go2-render"
        )
        self._viewer = None
        self._realtime = realtime
        self._running = True

        self._reset_to_home()
        if viewer:
            self._viewer = self._open_viewer()

        self._thread = threading.Thread(target=self._loop, daemon=True, name="go2-sim")
        self._thread.start()

    # ------------------------------------------------------------------ setup

    def _find_obstacle_geoms(self) -> set[int]:
        mujoco = self._mujoco
        found = set()
        for i in range(self._model.ngeom):
            name = mujoco.mj_id2name(self._model, mujoco.mjtObj.mjOBJ_GEOM, i)
            if name and name.startswith(_OBSTACLE_PREFIX):
                found.add(i)
        return found

    def _open_viewer(self):
        # Pre-flight: on a Linux box with no display, GLFW aborts the whole
        # process at C level rather than raising, so there is nothing to catch.
        # Check first and fail with something a student can act on.
        import os

        if platform.system() == "Linux" and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        ):
            raise RuntimeError(
                "No display found (DISPLAY and WAYLAND_DISPLAY are both unset), so the\n"
                "MuJoCo viewer window cannot open.\n\n"
                "If you are on a headless machine or over SSH without X forwarding,\n"
                "run without a window:\n\n"
                "    dog = Go2(sim=True, viewer=False)\n\n"
                "Everything except the window still works, including camera().\n"
                "For camera() on a headless box set MUJOCO_GL=egl (GPU) or "
                "MUJOCO_GL=osmesa (software).\n"
            )

        try:
            import mujoco.viewer

            return mujoco.viewer.launch_passive(
                self._model, self._data, show_left_ui=False, show_right_ui=False
            )
        except RuntimeError as exc:
            if platform.system() == "Darwin":
                raise RuntimeError(str(exc) + _MACOS_HINT) from exc
            raise RuntimeError(
                f"{exc}\n\nIf this machine has no display, use Go2(viewer=False).\n"
            ) from exc

    def _reset_to_home(self) -> None:
        mujoco = self._mujoco
        if self._model.nkey > 0:
            mujoco.mj_resetDataKeyframe(self._model, self._data, 0)
        else:  # pragma: no cover - every shipped scene has a keyframe
            mujoco.mj_resetData(self._model, self._data)
        self._write_pose(0.0, 0.0, 0.0, _STAND_HEIGHT)
        self._write_joints(gait.stand_vector())
        mujoco.mj_forward(self._model, self._data)

    # ------------------------------------------------------------- simulation

    def _write_pose(self, x: float, y: float, yaw: float, z: float) -> None:
        self._data.qpos[0] = x
        self._data.qpos[1] = y
        self._data.qpos[2] = z
        half = yaw / 2.0
        self._data.qpos[3] = math.cos(half)  # w
        self._data.qpos[4] = 0.0
        self._data.qpos[5] = 0.0
        self._data.qpos[6] = math.sin(half)  # z
        self._data.qvel[:] = 0.0

    def _write_joints(self, angles) -> None:
        start = self._joint_qpos0
        self._data.qpos[start : start + 12] = angles

    def _blocked(self) -> bool:
        """True if the body is currently intersecting an obstacle."""
        if not self._obstacle_geoms:
            return False
        for i in range(self._data.ncon):
            con = self._data.contact[i]
            if con.geom1 in self._obstacle_geoms or con.geom2 in self._obstacle_geoms:
                return True
        return False

    def _tick(self, dt: float) -> None:
        mujoco = self._mujoco
        with self._lock:
            if self._reset_requested:
                self._reset_to_home()
                self._reset_requested = False
                return
            vx, vy, wz = self._cmd
            mode = self._mode
            x, y, yaw = self._x, self._y, self._yaw
            self._clock += dt
            clock = self._clock
            self._tick_locked(mujoco, dt, vx, vy, wz, mode, x, y, yaw, clock)

    def _tick_locked(self, mujoco, dt, vx, vy, wz, mode, x, y, yaw, clock) -> None:
        if mode == "sit":
            self._write_pose(x, y, yaw, _STAND_HEIGHT * 0.55)
            self._write_joints(gait.sit_vector())
            mujoco.mj_forward(self._model, self._data)
            return

        speed = math.hypot(vx, vy)
        new_yaw = yaw + wz * dt
        # Body-frame velocity -> world frame.
        new_x = x + (vx * math.cos(new_yaw) - vy * math.sin(new_yaw)) * dt
        new_y = y + (vx * math.sin(new_yaw) + vy * math.cos(new_yaw)) * dt
        z = _STAND_HEIGHT + gait.body_bob(clock, speed)

        self._write_pose(new_x, new_y, new_yaw, z)
        self._write_joints(gait.pose_vector(clock, speed))
        mujoco.mj_forward(self._model, self._data)

        if self._blocked():
            # Refuse the translation but keep the turn, so a student can rotate
            # away from a wall instead of being stuck against it.
            self._write_pose(x, y, new_yaw, z)
            mujoco.mj_forward(self._model, self._data)
            if self._blocked():
                self._write_pose(x, y, yaw, z)
                mujoco.mj_forward(self._model, self._data)
                new_x, new_y, new_yaw = x, y, yaw
            else:
                new_x, new_y = x, y

        self._x, self._y, self._yaw = new_x, new_y, new_yaw

    def _loop(self) -> None:
        next_tick = time.perf_counter()
        last_sync = 0.0
        while self._running:
            self._tick(_SIM_DT)

            if self._viewer is not None:
                if not self._viewer.is_running():
                    self._running = False
                    break
                now = time.perf_counter()
                if now - last_sync >= _VIEWER_DT:
                    self._viewer.sync()
                    last_sync = now

            if self._realtime:
                next_tick += _SIM_DT
                delay = next_tick - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:  # fell behind; resync rather than spiral
                    next_tick = time.perf_counter()

    # ----------------------------------------------------------------- public

    def say(self, data: bytes, content_type: str = "audio/webm") -> str:
        clip_id = self.audio.add(data, content_type)
        self.audio.play(clip_id)
        return clip_id

    def reset(self) -> None:
        # Hand the work to the sim thread rather than touching MjData here:
        # reset() is called from an HTTP handler in service mode.
        with self._lock:
            self._cmd = (0.0, 0.0, 0.0)
            self._mode = "stand"
            self._x = self._y = self._yaw = 0.0
            self._clock = 0.0
            self._reset_requested = True
        for _ in range(100):                 # wait for the tick to apply it
            time.sleep(_SIM_DT)
            with self._lock:
                if not self._reset_requested:
                    return

    def stand(self) -> None:
        with self._lock:
            self._mode = "stand"
            self._cmd = (0.0, 0.0, 0.0)

    def sit(self) -> None:
        with self._lock:
            self._mode = "sit"
            self._cmd = (0.0, 0.0, 0.0)

    def move(self, vx: float, vy: float, wz: float) -> None:
        with self._lock:
            self._mode = "stand"
            self._cmd = (float(vx), float(vy), float(wz))

    def stop(self) -> None:
        with self._lock:
            self._cmd = (0.0, 0.0, 0.0)

    def pose(self) -> Pose:
        with self._lock:
            return Pose(self._x, self._y, _wrap(self._yaw))

    def velocity(self) -> Velocity:
        with self._lock:
            return Velocity(*self._cmd)

    def camera(self, width: int = 640, height: int = 480, camera: str = "head") -> np.ndarray:
        # Always render on the pool's single thread -- see _render_pool above.
        return self._render_pool.submit(self._render, width, height, camera).result()

    def _render(self, width: int, height: int, camera: str) -> np.ndarray:
        mujoco = self._mujoco
        if self._renderer is None or self._renderer.width != width or self._renderer.height != height:
            if self._renderer is not None:
                self._renderer.close()
            try:
                self._renderer = mujoco.Renderer(self._model, height=height, width=width)
            except Exception as exc:  # pragma: no cover - platform dependent
                raise RuntimeError(
                    f"Could not create an offscreen renderer: {exc}\n\n"
                    "On a headless Linux machine set MUJOCO_GL=egl (GPU) or "
                    "MUJOCO_GL=osmesa (software) before starting Python.\n"
                ) from exc
        with self._lock:
            # update_scene READS MjData; render() works off the copied scene
            # and is deliberately outside the lock so physics is not blocked
            # by the GPU.
            self._renderer.update_scene(self._data, camera=camera)
        return self._renderer.render()

    def close(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._renderer is not None:
            # Free the renderer on the thread that owns its EGL context.
            self._render_pool.submit(self._close_renderer).result(timeout=5.0)
        self._render_pool.shutdown(wait=True)
        if self._viewer is not None:
            self._viewer.close()
            self._viewer = None


    def _close_renderer(self) -> None:
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None


def _wrap(angle: float) -> float:
    """Wrap to (-pi, pi]."""
    return math.atan2(math.sin(angle), math.cos(angle))

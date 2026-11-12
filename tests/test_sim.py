"""Tests for the simulator backend.

Run headless and faster than realtime, so the suite finishes in seconds:
    pytest
"""

import math
import os

import numpy as np

import pytest

os.environ.setdefault("MUJOCO_GL", "egl")

from go2 import MAX_VX, Go2  # noqa: E402


@pytest.fixture
def dog():
    d = Go2(sim=True, viewer=False)
    yield d
    d.close()


def _settle(d, seconds=0.3):
    d.wait(seconds)


def test_starts_at_origin(dog):
    p = dog.pose()
    assert abs(p.x) < 1e-6 and abs(p.y) < 1e-6 and abs(p.yaw) < 1e-6


def test_forward_tracks_commanded_distance(dog):
    dog.stand()
    dog.move(vx=0.4)
    dog.wait(2.0)
    dog.stop()
    p = dog.pose()
    # Kinematic base: distance should match speed * time closely.
    assert p.x == pytest.approx(0.8, abs=0.08)
    assert abs(p.y) < 0.02


def test_turn_tracks_commanded_rate(dog):
    dog.stand()
    dog.move(wz=0.8)
    dog.wait(1.0)
    dog.stop()
    assert dog.pose().yaw == pytest.approx(0.8, abs=0.08)


def test_stop_halts_motion(dog):
    dog.move(vx=0.5)
    dog.wait(0.5)
    dog.stop()
    _settle(dog)
    before = dog.pose()
    dog.wait(0.5)
    after = dog.pose()
    assert math.hypot(after.x - before.x, after.y - before.y) < 1e-6


def test_velocity_is_clamped(dog):
    dog.move(vx=99.0)
    assert dog.velocity().vx == pytest.approx(MAX_VX)


def test_obstacle_blocks_motion(dog):
    """The arena has a wall at y=3.0; the dog must not pass through it."""
    dog.stand()
    dog.move(wz=1.5)
    dog.wait(math.pi / 2 / 1.5)      # face +y
    dog.stop()
    dog.move(vx=0.9)
    dog.wait(6.0)                    # would reach y=5.4 unobstructed
    dog.stop()
    assert dog.pose().y < 2.95, "walked through the wall"


def test_camera_returns_an_image(dog):
    img = dog.camera(width=160, height=120)
    assert img.shape == (120, 160, 3)
    assert img.dtype.name == "uint8"
    assert np.ptp(img) > 0, "image is a flat colour -- is the scene rendering?"


def test_sit_lowers_the_body(dog):
    dog.sit()
    _settle(dog)
    # qpos[2] is the base height; sitting is well below standing (0.27).
    assert dog._backend._data.qpos[2] < 0.2


def test_context_manager_closes_cleanly():
    with Go2(sim=True, viewer=False) as d:
        d.move(vx=0.2)
        d.wait(0.2)
    assert not d._backend._running


def test_real_mode_is_disabled_by_default(monkeypatch):
    """Hardware must be opt-in: a hosted notebook must not reach the robot."""
    monkeypatch.delenv("GO2_ALLOW_REAL", raising=False)
    with pytest.raises(PermissionError, match="disabled on this machine"):
        Go2(sim=False)


def test_real_backend_fails_with_a_clear_message(monkeypatch):
    """With the interlock lifted, it still fails -- step 2 is not built yet."""
    monkeypatch.setenv("GO2_ALLOW_REAL", "1")
    with pytest.raises(NotImplementedError, match="step 2"):
        Go2(sim=False)


def test_chase_camera_differs_from_head(dog):
    """The arena defines an outside 'chase' view as well as the robot's 'head'."""
    head = dog.camera(width=160, height=120, camera="head")
    chase = dog.camera(width=160, height=120, camera="chase")
    assert head.shape == chase.shape
    assert np.ptp(chase) > 0
    assert not np.array_equal(head, chase), "both cameras rendered the same image"


def test_web_viewer_encodes_a_frame(dog):
    """The browser viewer can turn a rendered frame into a JPEG."""
    pytest.importorskip("PIL")
    from go2.web import _Frames

    frames = _Frames(dog, width=160, height=120, fps=10, camera="chase")
    try:
        for _ in range(50):
            if frames.latest:
                break
            dog.wait(0.05)
        assert frames.latest, "no frame was produced"
        assert frames.latest[:2] == b"\xff\xd8", "not a JPEG"
    finally:
        frames.stop()


def test_rendering_is_safe_from_two_threads(dog):
    """An EGL context belongs to one thread; rendering must be serialised.

    Regression: the notebook panel renders on its own thread while show() and
    live() render on the kernel thread. Sharing the context across both raised
    EGLError(EGL_BAD_ACCESS) on eglMakeCurrent.
    """
    import threading

    errors: list[BaseException] = []

    def worker():
        try:
            for _ in range(5):
                dog.camera(width=96, height=72, camera="chase")
        except BaseException as exc:  # noqa: BLE001 - recording for the assert
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for _ in range(5):
        dog.camera(width=96, height=72, camera="head")
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"rendering failed off-thread: {errors[0]!r}"

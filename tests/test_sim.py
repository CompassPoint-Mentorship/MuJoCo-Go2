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


def test_real_backend_fails_with_a_clear_message():
    with pytest.raises(NotImplementedError, match="step 2"):
        Go2(sim=False)

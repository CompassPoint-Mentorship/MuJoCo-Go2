"""Tests for the simulator backend.

Run headless and faster than realtime, so the suite finishes in seconds:
    pytest
"""

import math
import os
import threading
import time

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


def test_frame_endpoint_serves_a_jpeg(dog):
    """Single-frame polling is what works behind jupyter-server-proxy.

    Regression: the page used an MJPEG /stream, which the proxy buffers, so
    the image never loaded inside JupyterLab.
    """
    pytest.importorskip("PIL")
    import urllib.request

    from go2.web import serve_background

    server, frames = serve_background(dog, host="127.0.0.1", port=8321, fps=20)
    try:
        for _ in range(60):
            if frames.latest:
                break
            dog.wait(0.05)
        with urllib.request.urlopen("http://127.0.0.1:8321/frame", timeout=5) as r:
            assert r.status == 200
            assert r.headers["Content-Type"] == "image/jpeg"
            body = r.read()
        assert body[:2] == b"\xff\xd8", "not a JPEG"
    finally:
        frames.stop()
        server.shutdown()
        server.server_close()


def test_reset_and_render_under_concurrent_load(dog):
    """reset() + camera() from other threads must not corrupt MjData.

    Regression: reset() ran mj_forward on the caller's thread and camera()
    read MjData while the sim loop wrote it. MuJoCo has no internal locking,
    so this segfaulted the process -- in service mode that silently killed
    every student's simulator.
    """
    import threading

    errors: list[BaseException] = []
    stop = threading.Event()

    def resetter():
        try:
            while not stop.is_set():
                dog.reset()
                dog.move(vx=0.4, wz=0.5)
                dog.wait(0.02)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def renderer():
        try:
            while not stop.is_set():
                dog.camera(width=96, height=72, camera="chase")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=resetter), threading.Thread(target=renderer),
               threading.Thread(target=renderer)]
    for t in threads:
        t.start()
    time.sleep(4.0)
    stop.set()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent access failed: {errors[0]!r}"
    dog.reset()
    p = dog.pose()
    assert abs(p.x) < 1e-6 and abs(p.y) < 1e-6, f"reset left the dog at {p}"


def test_say_stores_and_marks_a_clip(dog):
    """say() accepts bytes and makes the clip current for the viewer."""
    before = dog._backend.audio.state()["seq"]
    clip = dog.say(b"not really audio, but bytes are bytes")
    state = dog._backend.audio.state()
    assert state["clip"] == clip
    assert state["seq"] == before + 1
    assert dog._backend.audio.get(clip)[1].startswith(b"not really")


def test_say_accepts_a_file_path(dog, tmp_path):
    f = tmp_path / "hello.wav"
    f.write_bytes(b"RIFF....WAVEfmt ")
    clip = dog.say(f)
    kind, data = dog._backend.audio.get(clip)
    assert kind == "audio/wav", "content type should follow the extension"
    assert data.startswith(b"RIFF")


def test_say_rejects_an_oversized_clip(dog):
    from go2.audio import MAX_CLIP_BYTES

    with pytest.raises(ValueError, match="limit"):
        dog.say(b"x" * (MAX_CLIP_BYTES + 1))


def test_audio_store_drops_the_oldest_clips(dog):
    from go2.audio import MAX_CLIPS

    for i in range(MAX_CLIPS + 5):
        dog.say(f"clip {i}".encode())
    assert len(dog._backend.audio.ids()) == MAX_CLIPS


# ---------------------------------------------------------------------------
# Tricks
# ---------------------------------------------------------------------------

def test_backflip_is_one_full_turn_that_starts_and_ends_standing():
    """The animation itself, without a simulator: a loop, not a displacement."""
    from go2 import gait

    start_pitch, start_lift, start_angles = gait.backflip_pose(0.0)
    end_pitch, end_lift, end_angles = gait.backflip_pose(1.0)

    assert start_pitch == 0.0
    assert end_pitch == pytest.approx(-2 * math.pi)     # exactly one turn
    assert start_lift == 0.0 and end_lift == pytest.approx(0.0)
    assert start_angles == gait.stand_vector()
    assert end_angles == pytest.approx(gait.stand_vector())

    # Highest in the middle, and tucked there too.
    mid_pitch, mid_lift, mid_angles = gait.backflip_pose(0.5)
    assert mid_lift == pytest.approx(gait.FLIP_HEIGHT)
    assert mid_angles[1] > start_angles[1]              # knees pulled in


def test_backflip_turns_the_dog_over_and_puts_it_back(dog):
    """Mid-flight the dog is upside down; afterwards it is standing again."""
    dog.stand()
    dog.move(vx=0.4)
    dog.wait(0.5)
    dog.stop()
    before = dog.pose()

    seen = []

    def watch():
        for _ in range(80):
            qpos = dog._backend._data.qpos
            w, x, y, _z = qpos[3], qpos[4], qpos[5], qpos[6]
            seen.append((float(qpos[2]), 1.0 - 2.0 * (x * x + y * y)))
            time.sleep(0.02)

    watcher = threading.Thread(target=watch)
    watcher.start()
    dog.backflip()
    watcher.join()

    heights = [h for h, _ in seen]
    upright = [u for _, u in seen]
    assert max(heights) > 0.5, "the dog never left the ground"
    assert min(upright) < -0.9, "the dog never actually went over"

    after = dog.pose()
    assert after.x == pytest.approx(before.x), "a flip must not travel"
    assert after.y == pytest.approx(before.y)
    assert upright[-1] == pytest.approx(1.0, abs=1e-3), "it did not land upright"

    # And it is a working dog afterwards, not stuck in a trick.
    dog.move(vx=0.3)
    dog.wait(0.4)
    dog.stop()
    assert dog.pose().x > after.x


def test_a_backend_that_cannot_flip_says_so():
    """Backends inherit a clear refusal rather than an AttributeError."""
    from go2.backends.base import Backend

    with pytest.raises(NotImplementedError, match="backflip"):
        Backend.backflip(object())


def test_the_panel_plays_the_first_clip_recorded_into_it():
    """A fresh panel has no clip, and must still play the first recording.

    The page skips playback on its first poll so that reloading does not
    replay an old clip. That baseline has to be taken whether or not a clip
    exists yet -- when it was taken only inside the `if (a.clip)` branch, a
    panel opened before anything was recorded never took one, and the guard
    swallowed the student's first recording instead.
    """
    from go2 import web

    page = web._PAGE
    assert "let lastSeq = null" in page
    assert "if (lastSeq === null) { lastSeq = a.seq; return; }" in page


# ---------------------------------------------------------------------------
# Listening (the microphone is in the browser, not in this process)
# ---------------------------------------------------------------------------

def test_listen_waits_for_the_browser_and_returns_the_clip(dog):
    """dog.listen() asks the panel to record, then waits for what comes back."""
    store = dog._backend.audio
    before = store.state()["listen_seq"]

    def pretend_to_be_the_panel():
        # Poll like the page does, then upload as MediaRecorder would.
        for _ in range(50):
            if store.state()["listen_seq"] != before:
                dog.say(b"RIFFpretend-this-is-a-voice", content_type="audio/webm")
                return
            time.sleep(0.05)

    panel = threading.Thread(target=pretend_to_be_the_panel)
    panel.start()
    clip = dog.listen(0.2)
    panel.join()

    assert clip, "listen() did not return the clip the panel uploaded"
    assert store.state()["listen_seconds"] == pytest.approx(0.2)
    assert dog.say_again() == clip


def test_listen_gives_up_when_nobody_is_watching(dog, monkeypatch):
    """No panel open means no microphone. That must end, not hang."""
    from go2.backends import sim as sim_module

    monkeypatch.setattr(sim_module, "LISTEN_GRACE", 0.3)
    started = time.monotonic()
    assert dog.listen(0.1) is None
    assert time.monotonic() - started < 3.0


def test_the_panel_is_told_how_long_to_record(dog):
    """The page learns about a request by polling /audio/state."""
    import json
    import socket
    import urllib.request

    from go2.web import serve_background

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server, frames = serve_background(dog, host="127.0.0.1", port=port, fps=2)
    try:
        dog._backend.audio.ask_to_record(2.5)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/audio/state") as r:
            state = json.loads(r.read().decode())
        assert state["listen_seconds"] == pytest.approx(2.5)
        assert state["listen_seq"] >= 1
    finally:
        frames.stop()
        server.shutdown()
        server.server_close()


def test_a_backend_without_ears_says_so():
    from go2.backends.base import Backend

    with pytest.raises(NotImplementedError, match="listen"):
        Backend.listen(object())

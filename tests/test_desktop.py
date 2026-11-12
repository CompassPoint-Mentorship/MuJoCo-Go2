"""Tests for the laptop launcher (`go2-lab` / `go2-run`).

These cover the parts that are easy to get wrong and expensive to debug on a
student's machine: seeding their folder without eating their work, choosing
ports, and giving up quickly when a child process dies instead of making them
stare at a spinner for a minute.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import pytest

from go2 import desktop


def test_coursework_ships_with_the_package():
    # If this fails, a pip install from GitHub gives a student a simulator and
    # no notebook to open -- which looks like a broken install.
    assert desktop.COURSEWORK.is_dir()
    names = {p.name for p in desktop.COURSEWORK.iterdir()}
    assert "01_hello_dog.ipynb" in names


def test_seed_work_dir_copies_the_course_material(tmp_path):
    work = tmp_path / "Go2 Simulator"
    desktop.seed_work_dir(work)
    assert (work / "01_hello_dog.ipynb").exists()


def test_seed_work_dir_never_overwrites_student_work(tmp_path):
    work = tmp_path / "work"
    desktop.seed_work_dir(work)
    mine = work / "01_hello_dog.ipynb"
    mine.write_text("my own edits")

    desktop.seed_work_dir(work)   # e.g. the student launches again tomorrow

    assert mine.read_text() == "my own edits"


def test_free_port_is_actually_free():
    port = desktop._free_port()
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))   # raises if something grabbed it


def test_free_port_varies():
    # Two launches on one laptop must not collide.
    assert len({desktop._free_port() for _ in range(5)}) > 1


def test_wait_for_gives_up_as_soon_as_the_process_dies():
    # A child that exits immediately (port clash, missing dependency) should
    # cost the student no more than the poll interval, not the full timeout.
    proc = subprocess.Popen([sys.executable, "-c", "raise SystemExit(1)"])
    proc.wait()

    t0 = time.monotonic()
    ok = desktop._wait_for("http://127.0.0.1:1/never", proc=proc, timeout=30)
    elapsed = time.monotonic() - t0

    assert ok is False
    assert elapsed < 5, f"took {elapsed:.1f}s to notice a dead process"


def test_wait_for_times_out_on_a_dead_url():
    t0 = time.monotonic()
    assert desktop._wait_for("http://127.0.0.1:1/never", timeout=1.5) is False
    assert time.monotonic() - t0 < 10


def test_render_backend_falls_back_to_software_on_a_headless_linux(monkeypatch):
    monkeypatch.setattr(desktop.platform, "system", lambda: "Linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("MUJOCO_GL", raising=False)

    assert desktop._render_backend_env() == {"MUJOCO_GL": "osmesa"}


def test_render_backend_respects_an_explicit_choice(monkeypatch):
    monkeypatch.setattr(desktop.platform, "system", lambda: "Linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("MUJOCO_GL", "egl")

    assert desktop._render_backend_env() == {"MUJOCO_GL": "egl"}


def test_render_backend_leaves_macos_alone(monkeypatch):
    # macOS renders offscreen through CGL and needs nothing set; forcing
    # osmesa there would be slower and wrong.
    monkeypatch.setattr(desktop.platform, "system", lambda: "Darwin")
    monkeypatch.delenv("DISPLAY", raising=False)

    assert desktop._render_backend_env() == {}


def test_jupyter_command_comes_from_this_environment():
    cmd = desktop._jupyter_cmd()
    assert cmd
    # Never a bare name off PATH: that could be a different Python's Jupyter,
    # which would import a different go2.
    assert cmd[0].startswith(("/", sys.executable)) or cmd[0] == sys.executable


def test_go2_run_rejects_a_missing_script(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop.platform, "system", lambda: "Linux")
    assert desktop.run_script([str(tmp_path / "nope.py")]) == 2
    assert "no such file" in capsys.readouterr().err


def test_go2_run_executes_a_script(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(desktop.platform, "system", lambda: "Linux")
    script = tmp_path / "hello.py"
    script.write_text("print('ran as', __name__)")

    assert desktop.run_script([str(script)]) == 0
    assert "ran as __main__" in capsys.readouterr().out


def test_default_work_dir_is_somewhere_a_student_can_find(monkeypatch):
    monkeypatch.setattr(desktop.platform, "system", lambda: "Darwin")
    assert desktop.default_work_dir().parts[-2:] == ("Documents", "Go2 Simulator")


# ---------------------------------------------------------------------------
# the Go2 Lab window
# ---------------------------------------------------------------------------

def test_set_window_title_survives_a_non_tty(capsys):
    """Titling is cosmetic; a redirected stdout must not take the lab down."""
    desktop._set_window_title("Go2 Lab")
    assert capsys.readouterr().out == ""


def test_watch_returns_when_the_lab_exits():
    """_watch must notice the child is gone even with nothing on stdin.

    With no tty it is a plain poll loop, which is the path a piped or
    service-managed launch takes.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.4)"])
    t0 = time.monotonic()
    desktop._watch(proc, "http://127.0.0.1:1/never-opened")
    elapsed = time.monotonic() - t0
    assert proc.poll() is not None
    assert elapsed < 5.0, f"_watch hung for {elapsed:.1f}s after the lab exited"


def test_viewer_is_live_says_no_when_it_cannot_tell():
    """Every failure must answer "no": this only gates a warning message.

    A wrong "yes" tells a student a tab is open when none is, which sends
    them hunting through their windows for nothing.
    """
    assert desktop._viewer_is_live(None) is False
    # Nothing listening on this port -- refused, not a slow timeout.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    t0 = time.monotonic()
    assert desktop._viewer_is_live(f"http://127.0.0.1:{dead}/watchers") is False
    assert time.monotonic() - t0 < 3.0


def test_watchers_endpoint_tracks_frame_fetches():
    """/frame stamps the clock; /watchers reports how long ago."""
    import json
    import urllib.error
    import urllib.request

    from go2.api import Go2
    from go2.web import serve_background

    dog = Go2(sim=True, viewer=False)
    server, _frames = serve_background(dog, host="127.0.0.1", port=0, fps=5)
    port = server.server_address[1]
    try:
        url = f"http://127.0.0.1:{port}"
        assert json.loads(urllib.request.urlopen(f"{url}/watchers", timeout=5).read())["age"] is None
        try:
            urllib.request.urlopen(f"{url}/frame", timeout=5).read()
        except urllib.error.HTTPError as exc:
            # 503 "not ready" is fine: the first render may still be in
            # flight. Asking for a frame is what counts as looking.
            assert exc.code == 503
        age = json.loads(urllib.request.urlopen(f"{url}/watchers", timeout=5).read())["age"]
        assert age is not None and age < 2.0
    finally:
        server.shutdown()
        server.server_close()
        dog.close()

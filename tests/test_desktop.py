"""Tests for the laptop launcher (`go2-lab` / `go2-run`).

These cover the parts that are easy to get wrong and expensive to debug on a
student's machine: seeding their folder without eating their work, choosing
ports, and giving up quickly when a child process dies instead of making them
stare at a spinner for a minute.
"""

from __future__ import annotations

import json
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

    notes = desktop.seed_work_dir(work)   # e.g. they launch again tomorrow

    assert mine.read_text() == "my own edits"
    # ...and the fixed version arrives beside it rather than not at all.
    new = work / "01_hello_dog.updated.ipynb"
    assert new.exists()
    assert new.read_bytes() == (desktop.COURSEWORK / "01_hello_dog.ipynb").read_bytes()
    assert any("kept your" in n for n in notes)


def test_seed_work_dir_updates_a_file_the_student_never_touched(tmp_path):
    """The whole point: a fix upstream has to reach a laptop too."""
    work = tmp_path / "work"
    desktop.seed_work_dir(work)
    dst = work / "01_hello_dog.ipynb"

    # Pretend the course material changed under us.
    seeded = work / desktop.SEED / "01_hello_dog.ipynb"
    stale = b"the old version"
    dst.write_bytes(stale)
    seeded.write_bytes(stale)

    notes = desktop.seed_work_dir(work)

    assert dst.read_bytes() == (desktop.COURSEWORK / "01_hello_dog.ipynb").read_bytes()
    assert not (work / "01_hello_dog.updated.ipynb").exists()
    assert any("updated" in n for n in notes)


def test_seed_work_dir_leaves_the_students_own_files_alone(tmp_path):
    """Nothing is ever looked at unless the coursework folder names it."""
    work = tmp_path / "work"
    desktop.seed_work_dir(work)
    mine = work / "my_experiment.py"
    mine.write_text("dog.move(vx=9)")
    notebook = work / "my notes.ipynb"
    notebook.write_text("{}")

    desktop.seed_work_dir(work)
    desktop.seed_work_dir(work)

    assert mine.read_text() == "dog.move(vx=9)"
    assert notebook.read_text() == "{}"


def test_seed_work_dir_is_quiet_when_nothing_changed(tmp_path):
    work = tmp_path / "work"
    assert desktop.seed_work_dir(work)            # first time: all added
    assert desktop.seed_work_dir(work) == []      # second time: silence


def test_seed_work_dir_protects_the_updated_copy_once_it_is_used(tmp_path):
    work = tmp_path / "work"
    desktop.seed_work_dir(work)
    (work / "01_hello_dog.ipynb").write_text("my own edits")
    desktop.seed_work_dir(work)

    # The student starts working in the fixed copy we left them.
    theirs = work / "01_hello_dog.updated.ipynb"
    theirs.write_text("my work, in the new one")
    notes = desktop.seed_work_dir(work)

    assert theirs.read_text() == "my work, in the new one"
    assert any("and your" in n for n in notes)


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


def test_default_work_dir_is_somewhere_a_student_can_find(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(desktop.pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    assert desktop.default_work_dir().parts[-2:] == ("Documents", "Go2 Notebooks")

    # An install from before the rename keeps its folder, and with it the
    # student's work: a tidier name is not worth splitting someone's
    # notebooks across two directories.
    (tmp_path / "Documents" / "Go2 Simulator").mkdir(parents=True)
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


def test_coursework_hardcodes_no_simulator_port():
    """go2-lab picks a free port, so a hardcoded one only works on the server.

    This is exactly how it failed on a laptop: ConnectionRefusedError from a
    notebook cell, in a lesson about audio, which tells the student nothing.
    """
    for path in desktop.COURSEWORK.iterdir():
        if path.suffix in (".py", ".ipynb"):
            assert "127.0.0.1:8399" not in path.read_text(), f"{path.name} pins a port"


def test_say_again_replays_through_the_service():
    """What the notebook's audio cell does, over HTTP, as a student runs it."""
    import urllib.request

    from go2.api import Go2
    from go2.web import serve_background

    dog = Go2(sim=True, viewer=False)
    server, _frames = serve_background(dog, host="127.0.0.1", port=0, fps=5)
    port = server.server_address[1]
    try:
        remote = Go2(sim=True, url=f"http://127.0.0.1:{port}")
        assert remote.say_again() is None            # nothing recorded yet
        clip = remote.say(b"RIFFfake", content_type="audio/wav")
        before = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/audio/state", timeout=5).read())
        assert remote.say_again() == clip
        after = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/audio/state", timeout=5).read())
        # seq is what the viewer watches; without a bump nothing plays.
        assert after["seq"] > before["seq"]
        assert after["count"] == 1, "replay must not store a second copy"
    finally:
        server.shutdown()
        server.server_close()
        dog.close()


# ---------------------------------------------------------------------------
# which build is this
# ---------------------------------------------------------------------------

def test_build_id_says_so_when_there_is_no_stamp():
    """A source checkout must not invent a build number."""
    from go2 import build

    info = build.build_info()
    if info["sha"]:            # running from an installed copy
        assert info["build"] and info["build"] != build.UNKNOWN
    else:
        assert build.build_id() == build.UNKNOWN


def test_build_id_reads_the_stamp_the_installer_writes(tmp_path, monkeypatch):
    """The exact file install/mac.sh writes, parsed the way we claim to."""
    import importlib
    import sys as _sys

    pkg = tmp_path / "go2"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "build.py").write_text((desktop.PKG / "build.py").read_text())
    (pkg / "_build.py").write_text(
        '# Written by the installer at install time.\n'
        'BUILD = "20261112-192345Z"\n'
        'SOURCE = "https://example/main.tar.gz"\n'
        'SOURCE_SHA = "abc123def456"\n'
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in [n for n in _sys.modules if n == "go2" or n.startswith("go2.")]:
        monkeypatch.delitem(_sys.modules, name, raising=False)

    build = importlib.import_module("go2.build")
    assert build.build_info()["build"] == "20261112-192345Z"
    assert build.build_id() == "20261112-192345Z (abc123def456)"
    # Sorts chronologically as a plain string, which is the point of the format.
    assert "20261112-192345Z" < "20261113-010000Z"


def test_the_service_does_not_import_from_the_current_directory():
    """A `go2` folder in the launch directory must not shadow the install.

    This is how the viewer ended up reporting a different build from the
    launcher that started it.
    """
    import inspect

    src = inspect.getsource(desktop.run_lab)
    assert '"-P"' in src
    if sys.version_info >= (3, 11):
        out = subprocess.run(
            [sys.executable, "-P", "-c", "import sys; print(sys.path[0])"],
            capture_output=True, text=True, cwd="/tmp",
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() != "/tmp"

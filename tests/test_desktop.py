"""Tests for the laptop launcher (`go2-lab` / `go2-run`).

These cover the parts that are easy to get wrong and expensive to debug on a
student's machine: seeding their folder without eating their work, choosing
ports, and giving up quickly when a child process dies instead of making them
stare at a spinner for a minute.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time

import pytest

from go2 import course, desktop


def _unlock(path):
    """Stand in for a student who got write access to a handed-out file.

    They have to go out of their way for this -- JupyterLab will not do it --
    but a work dir seeded before the lock existed is in the same state, so
    the rules have to cope either way.
    """
    path.chmod(0o644)
    return path


def test_the_bundled_course_ships_with_the_package():
    # If this fails, a pip install from GitHub gives a student a simulator and
    # no notebook to open -- which looks like a broken install.
    assert course.units_dir(course.BUNDLED).is_dir()
    names = {p.name for p in course.units_dir(course.BUNDLED).iterdir()}
    assert "01_hello_dog.ipynb" in names


def test_seed_work_dir_copies_the_course_material(tmp_path):
    work = tmp_path / "Go2 Simulator"
    course.sync(work, course.BUNDLED)
    assert (work / "01_hello_dog.ipynb").exists()


def test_an_edited_example_moves_aside_and_is_never_destroyed(tmp_path):
    """The example always comes back; their work always survives.

    Only reachable from a work dir seeded before the lock, or a student who
    went around it -- but that is exactly when someone has real work in the
    file, so it keeps its content and its write permission.
    """
    work = tmp_path / "work"
    units = course.units_dir(course.BUNDLED)
    course.sync(work, course.BUNDLED)
    mine = work / "01_hello_dog.ipynb"
    _unlock(mine)
    mine.write_text("my own edits")

    notes = course.sync(work, course.BUNDLED)   # e.g. they launch again tomorrow

    rescued = work / "01_hello_dog.yours.ipynb"
    assert rescued.read_text() == "my own edits"
    assert rescued.stat().st_mode & 0o200, "they must be able to keep working in it"
    assert mine.read_bytes() == (units / "01_hello_dog.ipynb").read_bytes()
    assert mine.stat().st_mode & 0o777 == 0o444
    assert any("is now 01_hello_dog.yours.ipynb" in n for n in notes)


def test_a_second_unlock_does_not_overwrite_the_first_rescue(tmp_path):
    """Their work is never a file we are willing to lose."""
    work = tmp_path / "work"
    course.sync(work, course.BUNDLED)
    _unlock(work / "01_hello_dog.ipynb")
    (work / "01_hello_dog.ipynb").write_text("monday")
    course.sync(work, course.BUNDLED)

    _unlock(work / "01_hello_dog.ipynb")
    (work / "01_hello_dog.ipynb").write_text("tuesday")
    course.sync(work, course.BUNDLED)

    assert (work / "01_hello_dog.yours.ipynb").read_text() == "monday"
    assert (work / "01_hello_dog.yours-2.ipynb").read_text() == "tuesday"


def test_the_material_is_handed_over_read_only(tmp_path):
    """A file the student cannot save over is a file we can keep updating.

    This is what stops the folder filling with dated copies: the example
    stays pristine, so every release replaces it in place. Duplicate (which
    Jupyter does by saving a fresh file) gives them a writable one to work in.
    """
    work = tmp_path / "work"
    course.sync(work, course.BUNDLED)

    for name in ("01_hello_dog.ipynb", "02_walk_a_square.py"):
        mode = (work / name).stat().st_mode & 0o777
        assert mode == 0o444, f"{name} is {oct(mode)}, so a student can save over it"
    # The seed is ours to rewrite every time; locking it would stop sync dead.
    assert (work / course.SEED / "01_hello_dog.ipynb").stat().st_mode & 0o200


def test_an_older_unlocked_copy_is_locked_on_the_next_sync(tmp_path):
    """Work dirs seeded before the lock existed catch up without a fuss."""
    work = tmp_path / "work"
    course.sync(work, course.BUNDLED)
    unlocked = work / "01_hello_dog.ipynb"
    _unlock(unlocked)

    assert course.sync(work, course.BUNDLED) == []   # nothing to say about it
    assert unlocked.stat().st_mode & 0o777 == 0o444


def test_seed_work_dir_updates_a_file_the_student_never_touched(tmp_path):
    """The whole point: a fix upstream has to reach a laptop too."""
    work = tmp_path / "work"
    course.sync(work, course.BUNDLED)
    dst = work / "01_hello_dog.ipynb"

    # Pretend the course material changed under us.
    seeded = work / course.SEED / "01_hello_dog.ipynb"
    stale = b"the old version"
    _unlock(dst)
    dst.write_bytes(stale)
    seeded.write_bytes(stale)

    notes = course.sync(work, course.BUNDLED)

    assert dst.read_bytes() == (course.units_dir(course.BUNDLED) / "01_hello_dog.ipynb").read_bytes()
    assert not list(work.glob("01_hello_dog.new*.ipynb"))
    assert not list(work.glob("01_hello_dog.updated*.ipynb"))
    assert any("updated" in n for n in notes)


def test_seed_work_dir_leaves_the_students_own_files_alone(tmp_path):
    """Nothing is ever looked at unless the coursework folder names it."""
    work = tmp_path / "work"
    course.sync(work, course.BUNDLED)
    mine = work / "my_experiment.py"
    mine.write_text("dog.move(vx=9)")
    notebook = work / "my notes.ipynb"
    notebook.write_text("{}")

    course.sync(work, course.BUNDLED)
    course.sync(work, course.BUNDLED)

    assert mine.read_text() == "dog.move(vx=9)"
    assert notebook.read_text() == "{}"


def test_seed_work_dir_is_quiet_when_nothing_changed(tmp_path):
    work = tmp_path / "work"
    assert course.sync(work, course.BUNDLED)            # first time: all added
    assert course.sync(work, course.BUNDLED) == []      # second time: silence


def test_a_term_of_releases_does_not_fill_the_folder(tmp_path):
    """The complaint this design answers: one file per fix is a mess.

    Every release lands in the example's own name, because nothing can have
    changed it -- so five fixes leave no extra files at all.
    """
    work = tmp_path / "work"
    units = course.units_dir(course.BUNDLED)
    course.sync(work, course.BUNDLED)
    before = sorted(p.name for p in work.iterdir() if not p.name.startswith("."))

    for _ in range(5):          # a term of fixes
        (work / course.SEED / "01_hello_dog.ipynb").write_bytes(b"older")
        course.sync(work, course.BUNDLED)

    after = sorted(p.name for p in work.iterdir() if not p.name.startswith("."))
    assert after == before, f"clutter: {sorted(set(after) - set(before))}"
    assert (work / "01_hello_dog.ipynb").read_bytes() == (
        units / "01_hello_dog.ipynb").read_bytes()


def test_dated_copies_from_older_releases_are_tidied_away(tmp_path):
    """Folders that already collected them empty themselves again."""
    work = tmp_path / "work"
    units = course.units_dir(course.BUNDLED)
    course.sync(work, course.BUNDLED)
    _unlock(work / "01_hello_dog.ipynb")
    (work / "01_hello_dog.ipynb").write_text("my own edits")

    # Two copies left by the old dated scheme, recorded in the seed as ours
    # and never opened since.
    for stamp in ("20261112-1854", "20261112-2032"):
        old = work / f"01_hello_dog.updated-{stamp}.ipynb"
        shutil.copy2(units / "01_hello_dog.ipynb", old)
        shutil.copy2(old, work / course.SEED / old.name)

    notes = course.sync(work, course.BUNDLED)

    assert not list(work.glob("01_hello_dog.updated-*.ipynb"))
    assert (work / "01_hello_dog.ipynb").read_bytes() == (
        units / "01_hello_dog.ipynb").read_bytes()
    assert any("removed 2 older copies" in n for n in notes)


def test_a_dated_copy_the_student_worked_in_is_left_alone(tmp_path):
    """Tidying is for our files only -- theirs are never candidates."""
    work = tmp_path / "work"
    units = course.units_dir(course.BUNDLED)
    course.sync(work, course.BUNDLED)
    _unlock(work / "01_hello_dog.ipynb")
    (work / "01_hello_dog.ipynb").write_text("my own edits")

    old = work / "01_hello_dog.updated-20261112-1854.ipynb"
    shutil.copy2(units / "01_hello_dog.ipynb", old)
    shutil.copy2(old, work / course.SEED / old.name)
    old.write_text("hours of my homework")      # they worked in it

    course.sync(work, course.BUNDLED)

    assert old.read_text() == "hours of my homework"


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


def test_the_bundled_course_hardcodes_no_simulator_port():
    """go2-lab picks a free port, so a hardcoded one only works on the server.

    This is exactly how it failed on a laptop: ConnectionRefusedError from a
    notebook cell, in a lesson about audio, which tells the student nothing.
    """
    for path in course.units_dir(course.BUNDLED).iterdir():
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
    out = subprocess.run(
        [sys.executable, "-P", "-c", "import sys; print(sys.path[0])"],
        capture_output=True, text=True, cwd="/tmp",
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() != "/tmp"

"""Run the whole lab on your own machine: simulator, editor and viewer in one tab.

    go2-lab              start it, open the browser
    go2-lab --check      self-test; print what works and what does not
    go2-run script.py    run your own script (handles macOS's mjpython rule)

Why this is a browser page and not a native window
--------------------------------------------------
The obvious "local viewer" for macOS would be MuJoCo's own 3D window. This
launcher serves the same page the hosted labs serve instead, on 127.0.0.1, for
three reasons:

  * It is the *same* UI as the server. A student who works at home and in
    class learns one thing, not two.
  * Browsers treat ``localhost`` as a secure context, so the microphone works
    with no certificate at all -- the self-signed-TLS dance the shared server
    needs simply does not arise here.
  * macOS insists GUI windows own the main thread, which is why MuJoCo ships
    ``mjpython``. A page has no such rule, so one code path covers macOS and
    Ubuntu.

The native window is still there for anyone who wants it: ``go2-run`` re-execs
under ``mjpython`` on macOS automatically, so ``Go2(viewer=True)`` just works.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from . import course as go2course
from .build import build_id, build_info

PKG = pathlib.Path(__file__).resolve().parent

# Set by go2-run before it re-execs itself under mjpython, so the second run
# knows not to do it again.
_REEXEC_FLAG = "GO2_UNDER_MJPYTHON"


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _free_port() -> int:
    """A port nothing is using right now.

    Racy in principle -- something else could take it in the millisecond
    before we bind -- but on one laptop that does not happen, and it beats
    hardcoding ports that collide with whatever else the student is running.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def default_work_dir() -> pathlib.Path:
    """Where the student's notebooks live.

    Somewhere they can find in Finder, not hidden under a dotfile: half the
    point of a local install is being able to open the folder.
    """
    if platform.system() == "Darwin":
        docs = pathlib.Path.home() / "Documents"
        # Installs before the rename keep their folder: a student's notebooks
        # are not worth moving to tidy up a name, and a second folder with
        # half their work in it is worse than an old name.
        old = docs / "Go2 Simulator"
        return old if old.is_dir() else docs / "Go2 Notebooks"
    return pathlib.Path.home() / "go2-lab"


def seed_work_dir(work: pathlib.Path, course: str | None = None) -> list[str]:
    """Hand the course material over. The rules live in go2.course, once."""
    return go2course.sync(work, go2course.resolve(course))


def _jupyter_cmd() -> list[str]:
    """How to start JupyterLab from *this* interpreter's environment.

    Never a bare "jupyter-lab" off PATH: that could belong to some other
    Python, and would then import a go2 that is not this one.
    """
    local = pathlib.Path(sys.executable).with_name("jupyter-lab")
    if local.exists():
        return [str(local)]
    return [sys.executable, "-m", "jupyterlab"]


def _render_backend_env() -> dict[str, str]:
    """Pick an offscreen GL backend when the default cannot work.

    macOS renders offscreen through CGL and needs nothing set. On Linux the
    default is GLFW, which needs a display -- so over SSH or on a server, fall
    back to software rendering rather than crashing at C level.
    """
    if platform.system() == "Linux" and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return {"MUJOCO_GL": os.environ.get("MUJOCO_GL", "osmesa")}
    return {}


def _get(url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _wait_for(url: str, proc: subprocess.Popen | None = None,
              timeout: float = 60.0) -> bool:
    """Poll until the URL answers, or the process we are waiting on gives up.

    Watching the process matters: a child that exits immediately (a port
    clash, a missing dependency) would otherwise cost the student a full
    minute of staring at nothing before the timeout said so.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        if _get(url):
            return True
        time.sleep(0.5)
    return False


# --------------------------------------------------------------------------
# the simulator service, kept alive
# --------------------------------------------------------------------------

class _Supervisor(threading.Thread):
    """Run a child process, and bring it back if it dies.

    Same reasoning as the container entrypoint: a dead simulator leaves a
    frozen panel and a notebook full of ConnectionRefusedError for a student
    who did nothing wrong. Restarting loses the dog's position, which is
    recoverable; staying dead is not.
    """

    def __init__(self, cmd: list[str], env: dict[str, str]) -> None:
        super().__init__(daemon=True, name="go2-sim-supervisor")
        self._cmd = cmd
        self._env = env
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self.restarts = 0

    def run(self) -> None:
        while not self._stopping.is_set():
            # start_new_session so a Ctrl-C in the terminal reaches only us.
            # Otherwise the signal hits the service directly, it exits, and
            # this loop races to restart it while we are shutting down.
            proc = subprocess.Popen(self._cmd, env=self._env, start_new_session=True)
            with self._lock:
                self._proc = proc
            proc.wait()
            if self._stopping.is_set():
                return
            self.restarts += 1
            print(f"[go2] simulator exited; restarting (#{self.restarts})", file=sys.stderr)
            time.sleep(2)

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            proc = self._proc
        if proc is not None:
            _terminate(proc)


def _terminate(proc: subprocess.Popen, grace: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()


# --------------------------------------------------------------------------
# go2-lab
# --------------------------------------------------------------------------

def _require(module: str, extra_hint: str) -> None:
    try:
        __import__(module)
    except ImportError:
        raise SystemExit(
            f"'{module}' is missing, so the lab cannot start.\n\n"
            f"Install the lab extras:\n\n    {extra_hint}\n"
        )


def _die_on_signal(signum, _frame):
    """Turn a termination signal into the shutdown path we already have.

    Closing the Terminal window sends SIGHUP, and `kill` sends SIGTERM. Either
    one would otherwise kill this process outright, leaving JupyterLab and the
    simulator running with no window to stop them from -- which the student
    then hits as "port already in use" on the next launch.
    """
    raise KeyboardInterrupt(f"signal {signum}")


WINDOW_TITLE = "Go2 Lab"


def _set_window_title(text: str) -> None:
    """Name this terminal window after the thing it runs.

    Terminal.app otherwise titles the window after the file it is running --
    "go2-lab.command" -- which is a filename, not something a student put
    there on purpose. With several windows open, the one holding the lab
    should be the one that says so.
    """
    try:
        if sys.stdout.isatty():
            sys.stdout.write(f"\033]0;{text}\007")
            sys.stdout.flush()
    except (AttributeError, ValueError, OSError):  # redirected or closed
        pass


def _viewer_is_live(watchers_url: str | None, within: float = 3.0) -> bool:
    """Does a lab tab already appear to be open?

    The split page polls /frame continuously, so a fetch in the last few
    seconds means a browser is watching. Only used to warn before opening a
    second tab -- so every failure here answers "no" and says nothing.
    """
    if not watchers_url:
        return False
    try:
        with urllib.request.urlopen(watchers_url, timeout=1.0) as resp:
            age = json.loads(resp.read().decode()).get("age")
    except Exception:
        return False
    return isinstance(age, (int, float)) and age <= within


def _watch(lab: subprocess.Popen, url: str, watchers_url: str | None = None) -> None:
    """Wait for JupyterLab to exit; reopen the browser on every Return.

    The URL carries a per-launch token, so it is long and nobody is going to
    retype it. Close the tab and the only copy is somewhere up the scrollback
    -- which is a silly reason to have to restart the lab. Return brings the
    tab back.
    """
    interactive = os.name == "posix"
    try:
        interactive = interactive and sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        interactive = False

    while lab.poll() is None:
        if not interactive:
            time.sleep(0.5)
            continue
        try:
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
        except (OSError, ValueError):  # stdin went away under us
            interactive = False
            continue
        if not ready:
            continue
        if sys.stdin.readline() == "":
            # Ctrl-D or a closed stdin. Not a request to stop the lab -- just
            # the end of anyone pressing Return at us.
            interactive = False
            continue
        if _viewer_is_live(watchers_url):
            # Opening it anyway is still the useful thing -- it brings the
            # browser to the front, which is half of why Return gets pressed.
            # But two tabs means two copies of the same notebook, and Jupyter
            # handles that with "file changed on disk" dialogs, so say so.
            print("  note: a lab tab looks like it is already open -- check your")
            print("        other browser tabs and windows first. Opening another")
            print("        one gives you two copies of the same notebook.")
        print(f"  opening {url}")
        webbrowser.open(url)


def run_lab(args: argparse.Namespace) -> int:
    _require("PIL", 'pip install "go2[lab]"')
    _require("jupyterlab", 'pip install "go2[lab]"')
    _require("jupyter_server_proxy", 'pip install "go2[lab]"')

    _set_window_title(WINDOW_TITLE)
    # Line-buffer our own output. Python block-buffers stdout when it is not a
    # terminal, so anyone logging a launch to a file (a shared machine, a bug
    # report) got JupyterLab's stderr interleaved with nothing of ours until
    # the process ended.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):  # pragma: no cover - odd stdout
        pass

    work = pathlib.Path(args.work).expanduser() if args.work else default_work_dir()
    # The class's latest material first, then hand it over by the usual
    # rules. Skipped when someone named a pack on purpose -- a mentor testing
    # a draft should get the draft, not whatever the class site has.
    if not args.course_pack and not os.environ.get("GO2_COURSE"):
        pulled = go2course.pull()
        for note in pulled.notes:
            print(f"[go2] {note}")
        if pulled and go2course.engine_is_behind(pulled.engine_commit):
            print("[go2] A newer simulator is available for your class.")
            print("[go2]   When you have a few minutes, run the install line again:")
            print(f"[go2]   curl -fsSL {go2course.course_url().rsplit('/course', 1)[0]}"
                  "/CompassPoint-Mentorship/MuJoCo-Go2/main/install/mac.sh | bash")
    pack = go2course.resolve(args.course_pack)
    for note in go2course.sync(work, pack):
        print(f"[go2] {note}")

    sim_port = args.sim_port or _free_port()
    lab_port = args.port or _free_port()
    # A fresh token each launch. It costs the student nothing -- the launcher
    # opens the browser for them -- and it keeps a stray page in another
    # browser, or another program on this machine, from driving the lab.
    token = os.urandom(24).hex()

    child_env = {**os.environ, **_render_backend_env()}
    # Tells the simulator service it is on a laptop, and where the student's
    # notebooks are: it watches the class site and offers new material in the
    # panel. Not set when a pack was named on purpose -- a mentor testing a
    # draft must not be offered the class's version over the top of it.
    if not args.course_pack and not os.environ.get("GO2_COURSE"):
        child_env["GO2_COURSE_WORK"] = str(work)

    # -P keeps the current directory off sys.path. Without it the service
    # imports whatever `go2` happens to sit in the folder the lab was started
    # from -- so a mentor launching from a clone ran the clone in the viewer
    # and the installed copy in the launcher, which is the worst of both and
    # reports the wrong build.
    sim_cmd = [
        sys.executable, "-P", "-m", "go2.service",
        "--host", "127.0.0.1", "--port", str(sim_port),
        "--width", str(args.width), "--height", str(args.height),
        "--fps", str(args.fps),
    ]
    if args.scene:
        sim_cmd += ["--scene", args.scene]

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _die_on_signal)
        except (ValueError, AttributeError, OSError):  # pragma: no cover - not on Windows
            pass

    sim = _Supervisor(sim_cmd, child_env)
    lab: subprocess.Popen | None = None
    sim.start()

    # One try/finally around everything after the children exist. Ctrl-C can
    # land in any of the waits below, and every path out of here must leave no
    # JupyterLab and no simulator behind holding their ports.
    try:
        print(f"[go2] starting the simulator on 127.0.0.1:{sim_port} ...")
        if not _wait_for(f"http://127.0.0.1:{sim_port}/state"):
            raise SystemExit(
                "The simulator did not come up within 60s.\n"
                "Run 'go2-lab --check' to see which part is unhappy.\n"
            )
        print("[go2] simulator ready")

        lab_env = {
            **child_env,
            # Notebook kernels talk to the service rather than each building
            # their own dog, so the viewer shows what the student's code does.
            "GO2_SIM_URL": f"http://127.0.0.1:{sim_port}",
        }
        # The class's stylesheet, which is only served when asked for.
        from . import labcss
        labcss.install()

        lab_cmd = _jupyter_cmd() + [
            "--ip=127.0.0.1",
            f"--port={lab_port}",
            "--no-browser",
            f"--ServerApp.root_dir={work}",
            f"--ServerApp.token={token}",
            "--ServerApp.password=",
            "--LabApp.custom_css=True",
        ]
        lab = subprocess.Popen(lab_cmd, env=lab_env, start_new_session=True)

        if not _wait_for(f"http://127.0.0.1:{lab_port}/api/status?token={token}", proc=lab):
            raise SystemExit(
                "JupyterLab did not start. The messages above say why.\n"
            )

        url = f"http://127.0.0.1:{lab_port}/proxy/{sim_port}/split?token={token}"
        print()
        print("  " + "-" * 68)
        print("  Go2 Lab is running. Keep this window open.")
        print(f"  {go2course.describe(pack)}")
        print(f"  build {build_id()}")
        print()
        print(f"    lab        {url}")
        print(f"    notebooks  {work}")
        print()
        print("    Press Return here to open the lab in your browser again.")
        print("    Press Ctrl-C here, or close this window, to stop the lab.")
        print("  " + "-" * 68)
        print()
        if not args.no_browser:
            webbrowser.open(url)

        _watch(lab, url, f"http://127.0.0.1:{sim_port}/watchers")
        print("[go2] JupyterLab exited.", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n[go2] stopping ...")
    finally:
        if lab is not None:
            _terminate(lab)
        sim.stop()

    # Neither of the two things left on screen can be closed from here: a page
    # may only close a window that script opened, and this one was opened by
    # the operating system. So say what is safe to close, rather than leaving
    # a dead window and a tab full of reconnection errors to be puzzled over.
    print()
    print("  Go2 Lab stopped. Your notebooks are saved in")
    print(f"  {work}")
    if platform.system() == "Darwin":
        print("  You can close this window (Command-W) and the browser tab.")
    else:
        print("  You can close this window and the browser tab.")
    print()
    return 0


# --------------------------------------------------------------------------
# go2-lab --check
# --------------------------------------------------------------------------

def run_check() -> int:
    ok = True

    def line(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"  [{'ok' if good else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")

    print("Go2 simulator self-test\n")
    print(f"  build      {build_id()}")
    print(f"  python     {sys.version.split()[0]}  ({sys.executable})")
    print(f"  platform   {platform.system()} {platform.release()} {platform.machine()}")
    print()

    try:
        import mujoco
        line("mujoco", True, mujoco.__version__)
    except Exception as exc:
        line("mujoco", False, str(exc))
        print("\nWithout MuJoCo nothing else can work. Reinstall and try again.")
        return 1

    for mod, label in (("PIL", "pillow (JPEG encoding)"),
                       ("jupyterlab", "jupyterlab"),
                       ("jupyter_server_proxy", "jupyter-server-proxy")):
        try:
            __import__(mod)
            line(label, True)
        except ImportError as exc:
            line(label, False, str(exc))

    if platform.system() == "Darwin":
        mjp = pathlib.Path(sys.executable).with_name("mjpython")
        line("mjpython (for the native window)", mjp.exists(),
             str(mjp) if mjp.exists() else "not found -- go2-run --window will not work")

    # The part most likely to be wrong on a machine nobody has tested: can we
    # actually render offscreen? Everything the viewer does rests on this.
    os.environ.update(_render_backend_env())
    try:
        from .api import Go2
        t0 = time.monotonic()
        dog = Go2(sim=True, viewer=False)
        try:
            dog.stand()
            frame = dog.camera(width=320, height=240)
            line("offscreen rendering", frame.shape == (240, 320, 3), f"{frame.shape} in "
                 f"{time.monotonic() - t0:.1f}s")
            t0 = time.monotonic()
            dog.move(vx=0.5)
            dog.wait(1.0)
            pose = dog.pose()
            line("physics", pose.x > 0.1, f"walked to x={pose.x:.2f} in "
                 f"{time.monotonic() - t0:.1f}s wall clock")
        finally:
            dog.close()
    except Exception as exc:
        line("offscreen rendering", False, f"{type(exc).__name__}: {exc}")

    print()
    if ok:
        print("All good. Start the lab with:  go2-lab")
        return 0
    print("Something above failed. Send that output to your mentor.")
    return 1


# --------------------------------------------------------------------------
# go2-run
# --------------------------------------------------------------------------

def run_script(argv: list[str] | None = None) -> int:
    """Run a student's script, dealing with macOS's main-thread rule for them.

    MuJoCo's window has to own the main thread on macOS, which is why the
    wheel ships `mjpython`. Forgetting it is the single most common first
    failure, and the error arrives long after the student has stopped
    expecting one -- so re-exec rather than explain.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: go2-run <script.py> [args...]\n\n"
              "Runs your script. On macOS it re-runs itself under mjpython so\n"
              "Go2(viewer=True) can open its window.")
        return 0 if argv else 2

    if platform.system() == "Darwin" and not os.environ.get(_REEXEC_FLAG):
        mjpython = pathlib.Path(sys.executable).with_name("mjpython")
        if mjpython.exists():
            env = {**os.environ, _REEXEC_FLAG: "1"}
            return subprocess.call([str(mjpython), "-m", "go2.desktop_run", *argv], env=env)
        print("[go2] mjpython not found; running under plain python. "
              "Go2(viewer=True) will fail -- use Go2(viewer=False).", file=sys.stderr)

    script = pathlib.Path(argv[0]).expanduser()
    if not script.exists():
        print(f"go2-run: no such file: {script}", file=sys.stderr)
        return 2

    os.environ.update(_render_backend_env())
    sys.argv = [str(script), *argv[1:]]
    sys.path.insert(0, str(script.parent.resolve()))
    code = compile(script.read_text(encoding="utf-8"), str(script), "exec")
    exec(code, {"__name__": "__main__", "__file__": str(script)})
    return 0


# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="go2-lab",
        description="Run the Go2 simulator, JupyterLab and the viewer on this machine.",
    )
    ap.add_argument("--check", action="store_true",
                    help="self-test instead of starting; prints what works")
    ap.add_argument("--version", action="store_true",
                    help="print which build this is and exit")
    ap.add_argument("--work", default=None,
                    help=f"notebook folder (default: {default_work_dir()})")
    ap.add_argument("--port", type=int, default=0, help="JupyterLab port (default: a free one)")
    ap.add_argument("--sim-port", type=int, default=0, help="simulator port (default: a free one)")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=360)
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--scene", default=None, help="path to an alternative MJCF arena")
    ap.add_argument("--course", dest="course_pack", default=None,
                    help="course pack to hand out (default: $GO2_COURSE, "
                         "an installed pack, or the bundled one)")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = ap.parse_args(argv)

    if args.version:
        info = build_info()
        print(f"go2-lab build {info['build']}")
        if info["commit"]:
            print(f"  commit  {info['commit']}")
        if info["sha"]:
            print(f"  source  {info['sha']}  from {info['source']}")
        try:
            pack = go2course.resolve()
            print(f"  course  {go2course.describe(pack)}  [{pack}]")
        except FileNotFoundError:
            print("  course  none found")
        print(f"  python  {sys.version.split()[0]}  ({sys.executable})")
        print(f"  on      {platform.system()} {platform.release()} {platform.machine()}")
        return 0
    if args.check:
        return run_check()
    return run_lab(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Show the dog inside a Jupyter notebook.

    from go2 import Go2
    from go2.notebook import show, live

    dog = Go2(sim=True, viewer=False)     # no desktop window on a server
    dog.move(vx=0.4)
    live(dog, seconds=5)                  # animates in the cell output
    dog.stop()
    show(dog)                             # one still frame

There is no desktop window here: the frames are rendered offscreen and drawn
into the cell output, so this works over SSH, in a container, and on a machine
with no display at all.
"""

from __future__ import annotations

import io
import time

from .api import Go2


def _encode(rgb, fmt: str = "PNG") -> bytes:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Inline display needs Pillow:  pip install pillow"
        ) from exc
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format=fmt, quality=80)
    return buf.getvalue()


def _display():
    try:
        from IPython.display import Image, display
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "show()/live() only work inside Jupyter or IPython."
        ) from exc
    return Image, display


def show(
    dog: Go2,
    width: int = 640,
    height: int = 480,
    camera: str = "chase",
):
    """Draw one frame into the cell output.

    `camera="chase"` is the outside view that follows the dog; `"head"` is what
    the dog itself sees.
    """
    Image, display = _display()
    png = _encode(dog.camera(width=width, height=height, camera=camera))
    display(Image(data=png, format="png"))


def live(
    dog: Go2,
    seconds: float = 5.0,
    fps: int = 10,
    width: int = 640,
    height: int = 480,
    camera: str = "chase",
):
    """Animate the dog in the cell output for `seconds`.

    The dog keeps doing whatever it was last told to do -- this only watches.

    You rarely need this in hosted mode: the panel beside the notebook is
    already live. It is for a local install with no panel, or for deliberately
    capturing motion in the notebook output.
    """
    Image, display = _display()
    interval = 1.0 / max(1, fps)
    png = _encode(dog.camera(width=width, height=height, camera=camera), "JPEG")
    handle = display(Image(data=png, format="jpeg"), display_id=True)

    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        t0 = time.perf_counter()
        png = _encode(dog.camera(width=width, height=height, camera=camera), "JPEG")
        handle.update(Image(data=png, format="jpeg"))
        delay = interval - (time.perf_counter() - t0)
        if delay > 0:
            time.sleep(delay)
    return handle


def drive(dog: Go2, vx: float = 0.0, vy: float = 0.0, wz: float = 0.0,
          seconds: float = 3.0, watch: bool = False, **kwargs):
    """Move for a fixed time, then stop. Returns the resulting pose.

        drive(dog, vx=0.4, seconds=3)

    It draws nothing by default. In hosted mode the live panel beside the
    notebook already shows the dog, so rendering a second copy into every cell
    output just clutters the notebook and slows it down.

    Pass `watch=True` to animate it inline anyway -- useful on a local install
    with no panel open.
    """
    dog.move(vx=vx, vy=vy, wz=wz)
    try:
        if watch:
            live(dog, seconds=seconds, **kwargs)
        else:
            dog.wait(seconds)
    finally:
        dog.stop()
    return dog.pose()


# --------------------------------------------------------------------------
# Live panel: the viewer inside JupyterLab, same tab, same simulation.
# --------------------------------------------------------------------------

_PANELS: dict[int, tuple] = {}


def panel(
    dog: Go2,
    port: int = 8300,
    width: int = 960,
    height: int = 540,
    fps: int = 20,
    camera: str = "chase",
    inline_height: int = 560,
):
    """Open a live, drivable view of THIS dog inside the notebook.

        dog = Go2(sim=True, viewer=False)
        panel(dog)

    Unlike live(), this does not block: the view keeps updating while you run
    other cells, so you can watch the dog react to each command as you type it.

    It serves the same page as `python -m go2.web`, on 127.0.0.1, reached
    through jupyter-server-proxy at /proxy/<port>/ -- same browser tab, same
    login, no second port to open.

    Tip: to get the viewer and your code side by side, right-click the output
    and choose "Create New View for Cell Output", then drag that tab to the
    right half of the screen. JupyterLab keeps it live while you work.

    Returns the port actually used -- if the requested one is busy, the next
    free port is taken instead.
    """
    from IPython.display import IFrame, display

    from .web import serve_background

    existing = _PANELS.get(port)
    if existing is not None and existing[2] is dog:
        display(IFrame(src=f"/proxy/{port}/", width="100%", height=inline_height))
        return port
    if existing is not None:
        close_panel(port)

    # The requested port can be held by a previous kernel whose socket has not
    # been released yet, or by another notebook. Walk forward until one is
    # free rather than failing with "Address already in use" -- the student
    # did nothing wrong and has no way to act on that error.
    last_error: OSError | None = None
    for candidate in range(port, port + 20):
        if candidate in _PANELS:
            continue
        try:
            server, frames = serve_background(
                dog, host="127.0.0.1", port=candidate,
                width=width, height=height, fps=fps, camera=camera,
            )
        except OSError as exc:  # port busy; try the next one
            last_error = exc
            continue
        _PANELS[candidate] = (server, frames, dog)
        display(IFrame(src=f"/proxy/{candidate}/", width="100%", height=inline_height))
        return candidate

    raise RuntimeError(
        f"Could not find a free port for the viewer between {port} and {port + 19}. "
        f"Last error: {last_error}. Try close_panel() first, or restart the kernel."
    )


def close_panel(port: int = 8300) -> None:
    """Stop the panel's server and rendering thread."""
    entry = _PANELS.pop(port, None)
    if entry is None:
        return
    server, frames, _dog = entry
    frames.stop()
    server.shutdown()
    server.server_close()

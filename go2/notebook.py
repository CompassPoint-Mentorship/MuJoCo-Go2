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
    Use it after a move() to see the result, the way you would watch the
    viewer window.
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
          seconds: float = 3.0, **kwargs):
    """Move for a fixed time while watching, then stop. A one-line demo helper.

        drive(dog, vx=0.4, seconds=3)
    """
    dog.move(vx=vx, vy=vy, wz=wz)
    try:
        live(dog, seconds=seconds, **kwargs)
    finally:
        dog.stop()
    return dog.pose()

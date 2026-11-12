"""Watch and drive the simulated dog from a browser.

    python -m go2.web                 # then open http://<host>:8300/

Why this exists: the MuJoCo viewer needs a desktop window, which is no use on
a headless server or over SSH. This renders the scene offscreen and streams it
as MJPEG, so any browser on the network can watch -- and drive -- the dog.

It is deliberately dependency-light: stdlib HTTP, Pillow for JPEG encoding.
No WebRTC, no video codec, no NVENC session limits. At 960x540/20fps one
viewer costs a few percent of a core.

SECURITY: this binds 0.0.0.0 by default and has NO authentication, exactly
like the Isaac Sim viewer. Anyone who can reach the port can drive the dog.
It is a simulated dog, so the stakes are low -- but do not expose it to the
internet. Use --host 127.0.0.1 to keep it local.
"""

from __future__ import annotations

import argparse
import io
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .api import Go2

_BOUNDARY = "go2frame"

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Go2 simulator</title>
<!-- URLs below are RELATIVE on purpose. This page is served both at / and
     under a prefix (jupyter-server-proxy mounts it at /proxy/<port>/).
     An absolute "/stream" would resolve against the Jupyter root there and
     the video would silently fail to load. -->
<style>
  :root { color-scheme: dark; }
  body { margin:0; background:#111; color:#eee;
         font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
         display:flex; flex-direction:column; align-items:center; gap:12px; padding:16px; }
  img { max-width:100%; border-radius:8px; background:#000; }
  #keys { opacity:.75 }
  kbd { background:#222; border:1px solid #444; border-radius:4px; padding:1px 6px; }
  #pose { font-variant-numeric: tabular-nums; }
  .row { display:flex; gap:8px; flex-wrap:wrap; justify-content:center; }
  button { background:#222; color:#eee; border:1px solid #444; border-radius:6px;
           padding:8px 14px; font:inherit; cursor:pointer; }
  button:active { background:#333; }
</style>
</head>
<body>
  <img id="view" alt="simulator view"/>
  <div id="pose">pose: --</div>
  <div class="row">
    <button data-cmd="vx=0.4">forward</button>
    <button data-cmd="vx=-0.3">back</button>
    <button data-cmd="wz=0.8">turn left</button>
    <button data-cmd="wz=-0.8">turn right</button>
    <button data-cmd="stop=1">stop</button>
    <button data-cmd="sit=1">sit</button>
    <button data-cmd="stand=1">stand</button>
  </div>
  <div id="keys">drive with <kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd>,
    <kbd>space</kbd> to stop &mdash; hold to move</div>
<script>
// Frames are POLLED one at a time rather than taken from the MJPEG /stream
// endpoint. jupyter-server-proxy buffers multipart/x-mixed-replace, so a
// stream served through it never reaches the browser; individual requests
// proxy fine. Double-buffering through a preload Image avoids flicker.
const FPS = 20;
const view = document.getElementById('view');
function nextFrame() {
  const img = new Image();
  img.onload = () => { view.src = img.src; setTimeout(nextFrame, 1000 / FPS); };
  img.onerror = () => setTimeout(nextFrame, 500);
  img.src = 'frame?t=' + Date.now();
}
nextFrame();

const send = q => fetch('cmd?' + q);
document.querySelectorAll('button').forEach(b =>
  b.onclick = () => send(b.dataset.cmd));

const held = new Set();
const KEYS = {w:'vx=0.4', s:'vx=-0.3', a:'wz=0.8', d:'wz=-0.8'};
function push() {
  const parts = [...held].map(k => KEYS[k]).filter(Boolean);
  send(parts.length ? parts.join('&') : 'stop=1');
}
addEventListener('keydown', e => {
  const k = e.key.toLowerCase();
  if (k === ' ') { e.preventDefault(); held.clear(); send('stop=1'); return; }
  if (KEYS[k] && !held.has(k)) { held.add(k); push(); }
});
addEventListener('keyup', e => {
  const k = e.key.toLowerCase();
  if (held.delete(k)) push();
});
setInterval(async () => {
  try {
    const p = await (await fetch('pose')).json();
    document.getElementById('pose').textContent =
      `pose:  x ${p.x.toFixed(2)}   y ${p.y.toFixed(2)}   yaw ${p.yaw.toFixed(2)}`;
  } catch (_) {}
}, 200);
</script>
</body>
</html>
"""


class _Frames:
    """Renders the scene in one thread; serves the latest JPEG to many."""

    def __init__(self, dog: Go2, width: int, height: int, fps: int, camera: str) -> None:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(
                "The web viewer needs Pillow for JPEG encoding:\n"
                "    pip install -e '.[web]'\n"
            ) from exc
        self._Image = Image
        self.dog = dog
        self.width, self.height, self.camera = width, height, camera
        self.interval = 1.0 / max(1, fps)
        self.latest: bytes | None = None
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="go2-web-render")
        self._thread.start()

    def _loop(self) -> None:
        while self.running:
            t0 = time.perf_counter()
            rgb = self.dog.camera(width=self.width, height=self.height, camera=self.camera)
            buf = io.BytesIO()
            self._Image.fromarray(rgb).save(buf, format="JPEG", quality=80)
            self.latest = buf.getvalue()
            delay = self.interval - (time.perf_counter() - t0)
            if delay > 0:
                time.sleep(delay)

    def stop(self) -> None:
        self.running = False
        self._thread.join(timeout=2.0)


def _handler(dog: Go2, frames: _Frames):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # quieter console
            pass

        def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path in ("", "/"):
                self._send(_PAGE.encode(), "text/html; charset=utf-8")
            elif url.path == "/pose":
                p = dog.pose()
                body = f'{{"x":{p.x:.4f},"y":{p.y:.4f},"yaw":{p.yaw:.4f}}}'.encode()
                self._send(body, "application/json")
            elif url.path == "/cmd":
                self._command(parse_qs(url.query))
                self._send(b"ok", "text/plain")
            elif url.path == "/frame":
                jpeg = frames.latest
                if jpeg is None:
                    self._send(b"not ready", "text/plain", 503)
                else:
                    self._send(jpeg, "image/jpeg")
            elif url.path == "/stream":
                # Kept for direct use (curl, VLC, a plain browser tab). Do not
                # rely on it behind a proxy -- see the note in _PAGE.
                self._stream()
            else:
                self._send(b"not found", "text/plain", 404)

        def _command(self, q: dict[str, list[str]]) -> None:
            if "stop" in q:
                dog.stop()
                return
            if "stand" in q:
                dog.stand()
                return
            if "sit" in q:
                dog.sit()
                return
            def num(name: str) -> float:
                try:
                    return float(q.get(name, ["0"])[0])
                except ValueError:
                    return 0.0
            dog.move(vx=num("vx"), vy=num("vy"), wz=num("wz"))

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={_BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while frames.running:
                    jpeg = frames.latest
                    if jpeg is None:
                        time.sleep(0.05)
                        continue
                    self.wfile.write(
                        f"--{_BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                        f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                    )
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
                    time.sleep(frames.interval)
            except (BrokenPipeError, ConnectionResetError):
                pass  # viewer closed the tab

    return Handler


def serve_background(
    dog: Go2,
    host: str = "127.0.0.1",
    port: int = 8300,
    width: int = 960,
    height: int = 540,
    fps: int = 20,
    camera: str = "chase",
):
    """Serve an EXISTING dog from a background thread; return (server, frames).

    This is what the notebook helper uses. The caller already owns the dog, so
    the viewer shows the same simulation the notebook is driving -- not a
    second, unrelated one.

    Defaults to 127.0.0.1: inside JupyterLab the page is reached through
    jupyter-server-proxy at /proxy/<port>/, which is same-origin and already
    authenticated, so the port itself never needs to be exposed.
    """
    frames = _Frames(dog, width, height, fps, camera)
    server = ThreadingHTTPServer((host, port), _handler(dog, frames))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name=f"go2-web-{port}")
    thread.start()
    return server, frames


def serve(
    host: str = "0.0.0.0",
    port: int = 8300,
    width: int = 960,
    height: int = 540,
    fps: int = 20,
    camera: str = "chase",
    scene: str | None = None,
) -> None:
    dog = Go2(sim=True, viewer=False, scene=scene)
    dog.stand()
    frames = _Frames(dog, width, height, fps, camera)
    server = ThreadingHTTPServer((host, port), _handler(dog, frames))
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    print(f"Go2 simulator on http://{shown}:{port}/   (Ctrl-C to stop)")
    if host in ("0.0.0.0", ""):
        print("Listening on all interfaces, with no authentication.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        frames.stop()
        server.shutdown()
        dog.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Browser viewer for the Go2 simulator")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8300)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--camera", default="chase", help='"chase" (outside) or "head" (robot view)')
    ap.add_argument("--scene", default=None, help="path to an alternative MJCF arena")
    serve(**vars(ap.parse_args()))


if __name__ == "__main__":
    main()

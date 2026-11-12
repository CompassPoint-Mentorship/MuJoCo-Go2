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
from .audio import MAX_CLIP_BYTES

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
    <button data-cmd="reset=1">reset</button>
  </div>
  <div class="row">
    <button id="rec">● record your voice</button>
    <button id="play" disabled>▶ play it again</button>
    <span id="recstate"></span>
  </div>
  <audio id="speaker" hidden></audio>
  <div id="keys">drive with <kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd>,
    <kbd>space</kbd> to stop &mdash; hold to move</div>
<script>
// Frames are POLLED one at a time rather than taken from the MJPEG /stream
// endpoint. jupyter-server-proxy buffers multipart/x-mixed-replace, so a
// stream served through it never reaches the browser; individual requests
// proxy fine. Double-buffering through a preload Image avoids flicker.
// Polling a 960x540 JPEG at 20 fps is about 1 MB/s per viewer. That is free
// on a LAN and painful over any WAN link, and most of it is wasted on tabs
// nobody is looking at -- students leave them open all day. So: a modest
// frame rate, and stop entirely while the tab is hidden.
const FPS = 12;
const view = document.getElementById('view');
let pending = false;
function nextFrame() {
  if (pending) return;
  if (document.hidden) { pending = true; return; }   // resumed on visibilitychange
  const img = new Image();
  img.onload = () => { view.src = img.src; setTimeout(nextFrame, 1000 / FPS); };
  img.onerror = () => setTimeout(nextFrame, 500);
  img.src = 'frame?t=' + Date.now();
}
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && pending) { pending = false; nextFrame(); }
});
nextFrame();

const send = q => fetch('cmd?' + q);
document.querySelectorAll('button[data-cmd]').forEach(b =>
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
// --- recording -----------------------------------------------------------
// Captured in the browser with MediaRecorder, POSTed to the service, then
// played back by whoever is watching: the simulated dog has no speaker.
// On the real robot the same clip goes to the Go2's own speaker.
const recBtn = document.getElementById('rec');
const recState = document.getElementById('recstate');
let recorder = null, chunks = [];

recBtn.onclick = async () => {
  if (recorder && recorder.state === 'recording') { recorder.stop(); return; }
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    recState.textContent = 'recording needs a secure page (https or localhost)';
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recorder = new MediaRecorder(stream);
    chunks = [];
    recorder.ondataavailable = e => chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      recBtn.textContent = '● record your voice';
      const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
      recState.textContent = 'sending ' + Math.round(blob.size / 1024) + ' KB...';
      const r = await fetch('audio', { method: 'POST', body: blob,
                                       headers: { 'Content-Type': blob.type } });
      recState.textContent = r.ok ? 'the dog will say it' : 'upload failed';
    };
    recorder.start();
    recBtn.textContent = '■ stop';
    recState.textContent = 'recording...';
  } catch (err) {
    recState.textContent = 'microphone blocked: ' + err.name;
  }
};

// --- playback ------------------------------------------------------------
// seq bumps every time something says(), including from Python, so the dog
// speaks whether the clip was recorded here or sent from a notebook.
const speaker = document.getElementById('speaker');
const playBtn = document.getElementById('play');
let lastSeq = -1, lastClip = null;

// Browsers refuse to play audio on a page nobody has clicked yet, so a clip
// sent from a notebook can arrive before the student has touched this panel
// and be silently dropped. Say that out loud, and leave a button to press:
// the click is itself the gesture the browser was waiting for.
function play(clip) {
  speaker.src = 'audio/' + clip;
  speaker.play().then(() => { recState.textContent = 'playing'; })
                .catch(() => { recState.textContent = 'click ▶ play it again to hear it'; });
}

playBtn.onclick = () => { if (lastClip) play(lastClip); };

setInterval(async () => {
  try {
    const a = await (await fetch('audio/state')).json();
    if (a.clip && a.clip !== lastClip) { lastClip = a.clip; playBtn.disabled = false; }
    if (a.clip && a.seq !== lastSeq) {
      // Not on the first poll: a page reload should not replay an old clip.
      if (lastSeq !== -1) play(a.clip);
      lastSeq = a.seq;
    }
  } catch (_) {}
}, 500);

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


_SPLIT = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Go2 lab</title>
<style>
  html,body { margin:0; height:100%; background:#111; }
  #split { display:flex; height:100%; }
  iframe { border:0; height:100%; }
  #left  { flex:1 1 auto; }
  #grip  { flex:0 0 6px; cursor:col-resize; background:#333; }
  #grip:hover { background:#555; }
  #right { flex:0 0 __RIGHT__px; }
  /* Sits above both iframes, and so above JupyterLab's own modal: a dialog
     offering to reconnect to a server that has been deliberately shut down
     is worse than useless, and it blocks the page while it says it. */
  #gone { position:fixed; inset:0; display:none; place-items:center;
          background:rgba(17,17,17,.94); color:#eee; z-index:9999;
          font:16px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace; }
  #gone div { max-width:30rem; padding:0 1.5rem; text-align:center; }
  #gone b { display:block; font-size:19px; margin-bottom:.75rem; }
  #gone span { opacity:.7; }
</style>
</head>
<body>
<div id="split">
  <iframe id="left"  title="JupyterLab"></iframe>
  <div    id="grip"></div>
  <iframe id="right" title="simulator"></iframe>
</div>
<div id="gone" role="status"><div>
  <b>The Go2 Lab has stopped.</b>
  Your notebooks are saved. You can close this tab.
  <span><br><br>Start the lab again and open the new link it prints:
  the address changes every time.</span>
</div></div>
<script>
// Work out where Jupyter lives from OUR OWN path rather than assuming "/lab".
// This page is served at <base>/proxy/<port>/split, and <base> is "/" only
// when Jupyter is at the domain root. Behind a reverse proxy that mounts each
// student under a path (ServerApp.base_url=/alice/), hardcoding "/lab" would
// escape the prefix and 404.
const here = location.pathname;
const base = here.replace(/proxy\/\d+\/split\/?$/, '');
const token = new URLSearchParams(location.search).get('token') || '';
const q = token ? '?token=' + encodeURIComponent(token) : '';
document.getElementById('left').src  = base + 'lab' + q;
document.getElementById('right').src = './' + q;
</script>
<script>
// Say when the lab is gone, instead of leaving JupyterLab to offer a
// reconnection that cannot happen.
//
// The test is Jupyter's own status endpoint, which is what the launcher
// waits for at startup. A request that fails at the NETWORK level means
// Jupyter is gone, which is what "the lab has stopped" means. A failing
// status code means Jupyter is up and something behind the proxy is not --
// the supervisor is probably already fixing that, so it is not worth
// throwing a full-page notice at anybody.
const gone = document.getElementById('gone');
let misses = 0;
setInterval(async () => {
  try {
    await fetch(base + 'api/status' + q, { cache: 'no-store' });
    misses = 0;
    gone.style.display = 'none';
  } catch (_) {
    // Three in a row: a laptop waking up or a slow moment should not throw
    // a full-page notice at a student who is still working.
    if (++misses >= 3) gone.style.display = 'grid';
  }
}, 2000);
</script>

<script>
// Drag the grip to resize. Both panes are same-origin (everything is served
// through the Jupyter server), so no cross-origin framing is involved.
const grip = document.getElementById('grip'), right = document.getElementById('right');
let dragging = false;
grip.addEventListener('mousedown', e => { dragging = true; e.preventDefault();
  document.querySelectorAll('iframe').forEach(f => f.style.pointerEvents = 'none'); });
addEventListener('mouseup', () => { dragging = false;
  document.querySelectorAll('iframe').forEach(f => f.style.pointerEvents = 'auto'); });
addEventListener('mousemove', e => {
  if (!dragging) return;
  const w = Math.min(Math.max(window.innerWidth - e.clientX, 280), window.innerWidth - 280);
  right.style.flexBasis = w + 'px';
});
</script>
</body>
</html>
"""


def _audio_store(dog):
    """The clip store behind whichever backend this dog uses."""
    return dog._backend.audio  # noqa: SLF001 - the service owns its backend


def _split_page(query: str) -> str:
    """Lab on the left, the live dog on the right, in one page.

    Both iframes are same-origin: this page is itself served through
    jupyter-server-proxy, so the Jupyter server is the origin for both and no
    CSP relaxation is needed.

    The frame URLs are worked out in the browser from this page's own path, so
    the layout survives being mounted under a prefix by a reverse proxy.
    """
    return _SPLIT.replace("__RIGHT__", "620")


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

    def encode(self, rgb) -> bytes:
        buf = io.BytesIO()
        self._Image.fromarray(rgb).save(buf, format="JPEG", quality=80)
        return buf.getvalue()

    def _loop(self) -> None:
        while self.running:
            t0 = time.perf_counter()
            rgb = self.dog.camera(width=self.width, height=self.height, camera=self.camera)
            self.latest = self.encode(rgb)
            delay = self.interval - (time.perf_counter() - t0)
            if delay > 0:
                time.sleep(delay)

    def stop(self) -> None:
        self.running = False
        self._thread.join(timeout=2.0)


def _handler(dog: Go2, frames: _Frames):
    # When a frame was last fetched, i.e. when someone was last looking.
    # A list because the handler closes over it and stamps it per request.
    last_frame_at = [0.0]

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

        def do_POST(self) -> None:
            url = urlparse(self.path)
            if url.path != "/audio":
                self._send(b"not found", "text/plain", 404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                self._send(b'{"error":"empty upload"}', "application/json", 400)
                return
            if length > MAX_CLIP_BYTES:
                self._send(b'{"error":"clip too large"}', "application/json", 413)
                return
            data = self.rfile.read(length)
            ctype = self.headers.get("Content-Type") or "audio/webm"
            try:
                clip = dog.say(data, content_type=ctype)
            except ValueError as exc:
                self._send(f'{{"error":"{exc}"}}'.encode(), "application/json", 400)
                return
            self._send(f'{{"clip":"{clip}"}}'.encode(), "application/json")

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
            elif url.path == "/audio/state":
                st = _audio_store(dog).state()
                body = (f'{{"clip":{"null" if st["clip"] is None else chr(34)+st["clip"]+chr(34)},'
                        f'"seq":{st["seq"]},"count":{st["count"]}}}').encode()
                self._send(body, "application/json")
            elif url.path.startswith("/audio/"):
                clip = _audio_store(dog).get(url.path[len("/audio/"):])
                if clip is None:
                    self._send(b"no such clip", "text/plain", 404)
                else:
                    self._send(clip[1], clip[0])
            elif url.path == "/split":
                self._send(_split_page(url.query).encode(), "text/html; charset=utf-8")
            elif url.path == "/state":
                p, v = dog.pose(), dog.velocity()
                body = (
                    f'{{"x":{p.x:.5f},"y":{p.y:.5f},"yaw":{p.yaw:.5f},'
                    f'"vx":{v.vx:.5f},"vy":{v.vy:.5f},"wz":{v.wz:.5f}}}'
                ).encode()
                self._send(body, "application/json")
            elif url.path == "/camera":
                self._camera(parse_qs(url.query))
            elif url.path == "/watchers":
                # "Is anyone looking at the dog right now?" The split page
                # polls /frame many times a second, so a recent fetch means a
                # lab tab is open somewhere. go2-lab asks this before opening
                # another browser tab; nothing else depends on it.
                t = last_frame_at[0]
                age = "null" if t == 0.0 else f"{time.monotonic() - t:.2f}"
                self._send(f'{{"age":{age}}}'.encode(), "application/json")
            elif url.path == "/frame":
                last_frame_at[0] = time.monotonic()
                jpeg = frames.latest
                if jpeg is None:
                    self._send(b"not ready", "text/plain", 503)
                else:
                    self._send(jpeg, "image/jpeg")
            elif url.path == "/stream":
                # Kept for direct use (curl, VLC, a plain browser tab). Do not
                # rely on it behind a proxy -- see the note in _PAGE.
                last_frame_at[0] = time.monotonic()
                self._stream()
            else:
                self._send(b"not found", "text/plain", 404)

        def _camera(self, q: dict[str, list[str]]) -> None:
            """A single JPEG from any camera -- how RemoteBackend reads pixels."""
            def num(name: str, default: int) -> int:
                try:
                    return int(q.get(name, [str(default)])[0])
                except ValueError:
                    return default
            name = q.get("camera", ["head"])[0]
            rgb = dog.camera(width=num("width", 640), height=num("height", 480), camera=name)
            self._send(frames.encode(rgb), "image/jpeg")

        def _command(self, q: dict[str, list[str]]) -> None:
            if "say" in q:
                try:
                    _audio_store(dog).play(q["say"][0])
                except KeyError:
                    pass
                return
            if "reset" in q:
                dog.reset()
                return
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

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
import json
import pathlib
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .api import Go2
from .build import build_id
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
  #build { opacity:.35; font-size:11px; }
  .row { display:flex; gap:8px; flex-wrap:wrap; justify-content:center; }
  button { background:#222; color:#eee; border:1px solid #444; border-radius:6px;
           padding:8px 14px; font:inherit; cursor:pointer; }
  button:active { background:#333; }
  /* The one thing on this panel that is news rather than furniture. Amber,
     so it reads as "look at me" against a dark panel without looking like
     an error. Hidden until there is something to say. */
  /* The classroom strip: one place at the top of the simulator pane for
     everything the class -- not the dog -- has to say. Items come from the
     service; the page only knows how to draw an item, so a new classroom
     feature needs no new script here. */
  #classroom { width:100%; max-width:960px; display:flex; align-items:center;
               gap:10px; flex-wrap:wrap; padding:6px 10px; border:1px solid #2b2f33;
               border-radius:8px; background:#181b1e; font-size:13px; }
  #cr-title { font-weight:600; color:#cfd4d8; margin-right:auto; }
  #cr-items { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
  .cr-item { display:inline-flex; align-items:center; gap:8px; padding:3px 4px 3px 10px;
             border-radius:6px; border:1px solid #333; color:#9aa0a6; }
  .cr-item button { padding:3px 10px; font-size:12px; }
  .cr-item.news  { background:#3a2a05; border-color:#f2c94c; color:#ffe9a8; }
  .cr-item.news button  { background:#f2c94c; color:#1b1404; border-color:#f2c94c; font-weight:600; }
  .cr-item.ok    { border-color:#2f6b35; color:#d6f5d0; }
  .cr-item.alert { background:#3a1010; border-color:#e5484d; color:#ffd6d6; }
  #cr-message { width:100%; color:#d6f5d0; font-size:12px; }
</style>
</head>
<body>
  <!-- The classroom strip. Drawn from classroom/status: a title and a list of
       items, each with a tone and perhaps a button. Hidden while there is
       nothing to say, which on a laptop is never -- the course item is
       always there, so "up to date" never looks like "broken". -->
  <header id="classroom" hidden>
    <span id="cr-title">Classroom</span>
    <span id="cr-items"></span>
    <span id="cr-message" hidden></span>
  </header>
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
    <button data-cmd="backflip=1">backflip</button>
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
  <!-- The first question when a student reports something odd is which code
       they are running, and they are looking at this panel when they ask. -->
  <div id="build">build __BUILD__</div>
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

// `seconds` > 0 stops on its own -- that is how dog.listen() from a notebook
// works, since the microphone is here in the browser and not in Python.
async function startRecording(seconds) {
  if (recorder && recorder.state === 'recording') return false;
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    recState.textContent = 'recording needs a secure page (https or localhost)';
    return false;
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
    recState.textContent = seconds > 0
      ? 'recording ' + seconds + 's for your notebook...'
      : 'recording...';
    if (seconds > 0) {
      setTimeout(() => { if (recorder && recorder.state === 'recording') recorder.stop(); },
                 seconds * 1000);
    }
    return true;
  } catch (err) {
    recState.textContent = 'microphone blocked: ' + err.name;
    return false;
  }
}

recBtn.onclick = () => {
  if (recorder && recorder.state === 'recording') { recorder.stop(); return; }
  startRecording(0);        // 0 = until the button is pressed again
};

// --- playback ------------------------------------------------------------
// seq bumps every time something says(), including from Python, so the dog
// speaks whether the clip was recorded here or sent from a notebook.
const speaker = document.getElementById('speaker');
const playBtn = document.getElementById('play');
// null means "we have not polled yet", and it must not start at a number.
// The guard below skips playback on the first poll so that reloading the page
// does not replay an old clip. The old code only took that baseline inside
// the `if (a.clip)` branch -- but a panel opened before anything has been
// recorded has no clip, so the baseline was never taken and the guard ate the
// NEXT clip instead: every student's first recording, played to nobody.
let lastSeq = null, lastClip = null, lastListen = null, lastHush = null;

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
    // dog.listen() from a notebook raises listen_seq; the microphone is here,
    // so the page is what actually records. Same first-poll baseline rule as
    // the clip sequence below: never act on the state we arrived to.
    if (lastListen === null) { lastListen = a.listen_seq; }
    else if (a.listen_seq !== lastListen) {
      lastListen = a.listen_seq;
      startRecording(a.listen_seconds);
    }
    // dog.hush() stops the sound here, because here is where it is playing.
    // Same baseline rule again: arriving at a page mid-tune is not a reason
    // to stop it.
    if (lastHush === null) { lastHush = a.hush_seq; }
    else if (a.hush_seq !== lastHush) {
      lastHush = a.hush_seq;
      speaker.pause();
      speaker.currentTime = 0;
      recState.textContent = 'quiet';
    }
    if (lastSeq === null) { lastSeq = a.seq; return; }   // baseline, clip or not
    if (a.seq !== lastSeq) {
      lastSeq = a.seq;
      if (a.clip) play(a.clip);
    }
  } catch (_) {}
}, 500);

// --- the classroom strip ---------------------------------------------------
// The service decides WHAT to say; this only knows how to draw an item:
// {text, tone, action: {label, do}}. Text goes in with textContent, never
// innerHTML -- some of it is file names that came from the class site.
const cr = document.getElementById('classroom');
const crTitle = document.getElementById('cr-title');
const crItems = document.getElementById('cr-items');
const crMessage = document.getElementById('cr-message');
let crMessageTimer = null;

function drawClassroom(s) {
  const items = (s && s.items) || [];
  if (!items.length) { cr.hidden = true; return; }
  cr.hidden = false;
  crTitle.textContent = s.title || 'Classroom';
  crItems.replaceChildren(...items.map(item => {
    const el = document.createElement('span');
    el.className = 'cr-item ' + (item.tone || 'quiet');
    const text = document.createElement('span');
    text.textContent = item.text;
    el.append(text);
    if (item.action) {
      const btn = document.createElement('button');
      btn.textContent = item.action.label;
      btn.onclick = () => classroomAction(item.action, btn, text);
      el.append(btn);
    }
    return el;
  }));
}

function sayInClassroom(message) {
  crMessage.textContent = message;
  crMessage.hidden = false;
  clearTimeout(crMessageTimer);
  crMessageTimer = setTimeout(() => { crMessage.hidden = true; }, 20000);
}

async function classroomAction(action, btn, text) {
  btn.disabled = true;
  if (action.busy) text.textContent = action.busy;
  try {
    const r = await fetch('classroom/action/' + encodeURIComponent(action.do),
                          { method: 'POST' });
    const out = await r.json();
    drawClassroom(out);
    if (out.message) sayInClassroom(out.message);
  } catch (_) {
    sayInClassroom('could not reach the class site; try again later');
  }
  btn.disabled = false;
}

async function pollClassroom() {
  try {
    const r = await fetch('classroom/status');
    if (r.ok) drawClassroom(await r.json());
  } catch (_) {}
}
pollClassroom();
setInterval(pollClassroom, 60000);

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


def _panel_page() -> str:
    """The viewer panel, stamped with the build it is being served by."""
    return _PAGE.replace("__BUILD__", build_id())


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


def _handler(dog: Go2, frames: _Frames, watcher=None):
    classroom = Classroom(watcher)
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
            if url.path.startswith("/classroom/action/"):
                name = url.path[len("/classroom/action/"):]
                handler = classroom.actions.get(name)
                if handler is None:
                    self._send(b'{"error":"no such action"}', "application/json", 404)
                    return
                message = handler()
                body = {**classroom.status(), "message": message}
                self._send(json.dumps(body).encode(), "application/json")
                return
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
                self._send(_panel_page().encode(), "text/html; charset=utf-8")
            elif url.path == "/classroom/status":
                self._send(json.dumps(classroom.status()).encode(), "application/json")
            elif url.path == "/pose":
                p = dog.pose()
                body = f'{{"x":{p.x:.4f},"y":{p.y:.4f},"yaw":{p.yaw:.4f}}}'.encode()
                self._send(body, "application/json")
            elif url.path == "/cmd":
                q = parse_qs(url.query)
                if "listen" in q:
                    # Blocks while the browser records, then answers with the
                    # clip, so a notebook's dog.listen() is one round trip.
                    try:
                        seconds = float(q["listen"][0])
                    except ValueError:
                        seconds = 3.0
                    clip = dog.listen(seconds)
                    body = ("null" if clip is None else '"' + clip + '"')
                    self._send(f'{{"clip":{body}}}'.encode(), "application/json")
                    return
                self._command(q)
                self._send(b"ok", "text/plain")
            elif url.path == "/audio/state":
                st = _audio_store(dog).state()
                clip = "null" if st["clip"] is None else '"' + st["clip"] + '"'
                body = (f'{{"clip":{clip},"seq":{st["seq"]},"count":{st["count"]},'
                        f'"listen_seq":{st["listen_seq"]},'
                        f'"listen_seconds":{st["listen_seconds"]},'
                        f'"hush_seq":{st["hush_seq"]}}}').encode()
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
            if "hush" in q:
                dog.hush()
                return
            if "backflip" in q:
                dog.backflip()
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
    watcher=None,
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
    server = ThreadingHTTPServer((host, port), _handler(dog, frames, watcher))
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
    watcher = _course_watcher()
    server = ThreadingHTTPServer((host, port), _handler(dog, frames, watcher))
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    print(f"Go2 simulator on http://{shown}:{port}/   (Ctrl-C to stop)")
    if host in ("0.0.0.0", ""):
        print("Listening on all interfaces, with no authentication.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        if watcher:
            watcher.stop()
        frames.stop()
        server.shutdown()
        dog.close()


class Classroom:
    """What the strip at the top of the simulator pane says, and does.

    Each feature contributes ITEMS -- {text, tone, action} -- and ACTIONS,
    named callables a button can trigger. The page draws whatever it is
    given, so adding a classroom feature (an announcement, an "I'm stuck"
    button, progress) means adding an item here, not another script.

    Tones: "quiet" for status, "news" for something to act on, "ok" for a
    success, "alert" for a problem.
    """

    def __init__(self, watcher=None):
        self.watcher = watcher
        self.actions: dict = {}
        if watcher is not None:
            self.actions["course-check"] = self._course_check
            self.actions["course-get"] = self._course_get

    def status(self) -> dict:
        items = []
        if self.watcher is not None:
            items += self._course_items()
        return {"title": self._title(), "items": items}

    # ------------------------------------------------------------- course

    def _title(self) -> str:
        if self.watcher is None:
            return "Classroom"
        try:
            from . import course
            meta = course.manifest(course.resolve()).get("course", {})
            return str(meta.get("title") or "Classroom")
        except Exception:  # noqa: BLE001 - a title is never worth an error
            return "Classroom"

    def _course_items(self) -> list[dict]:
        st = self.watcher.status()
        new = st.get("new") or []
        items = []
        if new:
            text = (f"New from your class: {new[0]}" if len(new) == 1
                    else f"{len(new)} new files from your class")
            items.append({"id": "course", "tone": "news", "text": text,
                          "action": {"label": "get it", "do": "course-get",
                                     "busy": "getting it..."}})
        elif st.get("online"):
            items.append({"id": "course", "tone": "quiet",
                          "text": f"course up to date \u00b7 checked {_ago(st.get('checked'))}",
                          "action": {"label": "check now", "do": "course-check",
                                     "busy": "checking..."}})
        else:
            items.append({"id": "course", "tone": "quiet",
                          "text": ("offline \u2014 using the course material you already have"
                                   if st.get("checked") else "checking for new course material..."),
                          "action": {"label": "check now", "do": "course-check",
                                     "busy": "checking..."}})
        if st.get("engine_behind"):
            items.append({"id": "engine", "tone": "news",
                          "text": "A newer simulator is ready \u2014 run the install line again when you have a few minutes"})
        return items

    def _course_check(self) -> str | None:
        self.watcher.refresh()
        return None

    def _course_get(self) -> str:
        notes = self.watcher.update_now()
        return (" \u00b7 ".join(notes) +
                " \u2014 if one of these is open, close it and open it again.")


def _ago(t) -> str:
    if not t:
        return "not yet"
    minutes = int((time.time() - float(t)) // 60)
    return "just now" if minutes < 1 else ("1 min ago" if minutes == 1 else f"{minutes} min ago")


def _course_watcher():
    """A background check for new course material -- on a laptop only.

    The laptop launcher sets GO2_COURSE_WORK to the student's notebook folder.
    A hosted lab never sets it: there the mentor's sync is how material
    arrives, and a second route would only make two students in the same
    class disagree about what they have.
    """
    work = os.environ.get("GO2_COURSE_WORK")
    if not work:
        return None
    from . import course

    if course.course_url().lower() in ("", "off", "none"):
        return None
    # Ten minutes by default: a manifest is a few hundred bytes, so this is
    # about freshness, not cost. GO2_COURSE_CHECK_EVERY shortens it for a
    # mentor checking that a change arrives.
    try:
        every = float(os.environ.get("GO2_COURSE_CHECK_EVERY", "600"))
    except ValueError:
        every = 600.0
    return course.Watcher(pathlib.Path(work), every=max(every, 5.0)).start()


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

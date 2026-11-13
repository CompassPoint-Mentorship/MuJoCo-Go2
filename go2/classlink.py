"""A laptop's link to its class: who this student is, and handing work in.

Everything else on a laptop works with no identity at all -- the install line
is public, the course pull is public, and the lab runs offline. This module
is the one part that has to know *whose* laptop it is, because handing in
homework (and, later, reading a mentor's comments) is personal.

Signing in is the device flow a TV uses to sign in to a video service:

  1. the laptop asks the class site for a pair of codes -- a short one for a
     browser and a long one only the laptop knows;
  2. the student's browser opens /connect?code=<short>, where they are
     usually already signed in, and they click yes;
  3. the laptop, which has been asking "approved yet?" every two seconds
     with the long code, receives a key.

The key never travels through a URL or a browser, and the class site keeps
only its hash. It can hand in homework for this student and read what is
theirs; it cannot sign in to the website, so a lost laptop costs a mentor
one click in the console, not a password reset.

Standard library only, like the rest of what a laptop installs.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import course

TIMEOUT = 8.0
HANDIN_SUFFIXES = {".ipynb", ".py", ".md", ".txt", ".csv", ".json",
                   ".png", ".jpg", ".jpeg"}


def site_url() -> str:
    """The class site, derived from where course material comes from."""
    explicit = os.environ.get("GO2_SITE_URL")
    if explicit:
        return explicit.rstrip("/")
    base = course.course_url()
    return base[: -len("/course")] if base.endswith("/course") else base


def identity_path() -> pathlib.Path:
    return course.installed_courses_dir().parent / "identity.json"


def load_identity() -> dict | None:
    try:
        data = json.loads(identity_path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("key"):
        return None
    if data.get("site") and data["site"] != site_url():
        return None            # a key for a different class site means nothing here
    return data


def save_identity(data: dict) -> None:
    """Written readable by this user only: the key is a credential."""
    path = identity_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)
    path.chmod(0o600)


def forget_identity() -> None:
    identity_path().unlink(missing_ok=True)


def _request(method: str, url: str, *, body: bytes | None = None,
             headers: dict | None = None, timeout: float = TIMEOUT) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"User-Agent": "go2-lab", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}


def _json(method: str, url: str, payload: dict, **kw) -> tuple[int, dict]:
    return _request(method, url, body=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"}, **kw)


def _multipart(field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = f"go2-{uuid.uuid4().hex}"
    safe = filename.replace('"', "")
    body = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; filename="{safe}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n").encode()
    body += data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


class Link:
    """This laptop's standing with the class site. Thread-safe."""

    def __init__(self, work: pathlib.Path):
        self.work = pathlib.Path(work)
        self._lock = threading.Lock()
        self._pending: dict | None = None
        self._note = ""
        self._feedback: tuple[float, dict] | None = None

    # ------------------------------------------------------------ who

    def who(self) -> dict | None:
        return load_identity()

    def state(self) -> dict:
        with self._lock:
            pending = dict(self._pending) if self._pending else None
            note = self._note
        return {"identity": self.who(), "pending": pending, "note": note}

    # -------------------------------------------------------- sign in

    def start_connect(self) -> str:
        """Ask the site for codes and start waiting. Returns the browser URL."""
        status, body = _json("POST", f"{site_url()}/api/connect/start",
                             {"label": socket.gethostname()[:80]})
        if status != 200 or "device_code" not in body:
            raise ConnectionError(body.get("error") or f"the class site answered {status}")
        with self._lock:
            self._pending = {"user_code": body["user_code"],
                             "verify_url": body["verify_url"],
                             "expires": time.time() + float(body.get("expires_in", 600))}
            self._note = ""
        threading.Thread(target=self._wait, args=(body["device_code"],
                                                  float(body.get("interval", 2))),
                         name="class-connect", daemon=True).start()
        return body["verify_url"]

    def _wait(self, device_code: str, interval: float) -> None:
        while True:
            with self._lock:
                pending = self._pending
            if not pending or time.time() > pending["expires"]:
                with self._lock:
                    if self._pending is pending:
                        self._pending = None
                        self._note = "sign-in timed out; press sign in again"
                return
            try:
                status, body = _json("POST", f"{site_url()}/api/connect/poll",
                                     {"device_code": device_code})
            except (urllib.error.URLError, OSError):
                status, body = 0, {}
            state = body.get("status")
            if state == "approved" and body.get("key"):
                save_identity({"key": body["key"], "slug": body.get("slug"),
                               "name": body.get("name"), "site": site_url()})
                with self._lock:
                    self._pending = None
                    self._note = ""
                return
            if state in ("denied", "expired", "unknown", "collected"):
                with self._lock:
                    self._pending = None
                    self._note = ("not connected -- the browser said no"
                                  if state == "denied" else
                                  "sign-in did not finish; press sign in again")
                return
            time.sleep(interval)

    def sign_out(self) -> None:
        forget_identity()
        with self._lock:
            self._pending = None
            self._note = ""

    # -------------------------------------------------------- feedback

    def feedback(self, max_age: float = 60.0) -> dict:
        """Unseen feedback from a mentor, asked of the site at most once a minute.

        The strip asks for its status often -- every three seconds while a
        sign-in is pending -- and the class site should not hear about it.
        Offline, or not signed in, there is simply nothing new to show.
        """
        identity = self.who()
        if not identity:
            return {"unseen": 0}
        with self._lock:
            cached = self._feedback
        if cached and time.time() - cached[0] < max_age:
            return cached[1]
        try:
            status, body = _request("GET", f"{site_url()}/api/comments",
                                    headers={"Authorization": f"Bearer {identity['key']}"})
        except (urllib.error.URLError, OSError):
            status, body = 0, {}
        if status == 401:
            forget_identity()
            body = {"unseen": 0}
        elif status != 200:
            body = cached[1] if cached else {"unseen": 0}
        with self._lock:
            self._feedback = (time.time(), body)
        return body

    # --------------------------------------------------------- hand in

    def candidates(self) -> list[str]:
        """The student's own files, newest first.

        Read-only files are the class's examples, handed out by sync; what a
        student hands in is something they made -- a duplicate, a script, an
        answer -- so those are left out of the list.
        """
        if not self.work.is_dir():
            return []
        files = [p for p in self.work.iterdir()
                 if p.is_file() and not p.name.startswith(".")
                 and p.suffix.lower() in HANDIN_SUFFIXES
                 and os.access(p, os.W_OK)]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.name for p in files[:25]]

    def hand_in(self, name: str) -> str:
        """Send a copy of one file. Returns a line to show the student."""
        identity = self.who()
        if not identity:
            raise PermissionError("sign in first")
        if name not in self.candidates():
            raise FileNotFoundError(f"{name} is not one of your files")
        data = (self.work / name).read_bytes()
        body, ctype = _multipart("file", name, data)
        status, reply = _request(
            "POST", f"{site_url()}/api/handin", body=body, timeout=TIMEOUT * 4,
            headers={"Content-Type": ctype,
                     "Authorization": f"Bearer {identity['key']}"})
        if status == 401:
            # Disconnected from the console, or the student was removed:
            # the key is dead, so stop presenting it.
            forget_identity()
            raise PermissionError("this laptop was disconnected; sign in again")
        if status != 200:
            raise ConnectionError(reply.get("error") or f"the class site answered {status}")
        return f"handed in {name} ✓"

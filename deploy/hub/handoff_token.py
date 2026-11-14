"""The pass that carries a student from the classroom site into JupyterHub.

The classroom site is the only place anyone signs in. When a signed-in
student opens their online lab, it writes a pass saying who they are, signs
it with a secret the hub shares, and sends the browser to the hub with it.
The hub checks the signature and the expiry, refuses a pass it has already
seen, and signs them in -- so the hub never shows a login page, and no lab
link ever carries a token.

    payload = base64url(json{"u": username, "exp": unix_time, "n": nonce})
    pass    = payload + "." + base64url(HMAC-SHA256(secret, payload))

Sixty seconds and single use: long enough for a redirect, useless if it ends
up in a browser history or a log.

Standard library only. The classroom site has its own copy of make_pass();
tests/test_classroom.py checks the two agree.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time

TTL = 60
USERNAME = re.compile(r"^[a-z][a-z0-9-]{1,30}$")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_pass(secret: str, username: str, ttl: int = TTL, now: float | None = None) -> str:
    if not secret or not USERNAME.match(username):
        raise ValueError("cannot write a pass for that")
    body = {"u": username, "exp": int((now or time.time()) + ttl),
            "n": secrets.token_urlsafe(12)}
    payload = _b64(json.dumps(body, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read_pass(secret: str, token: str, now: float | None = None) -> dict | None:
    """The pass's contents if it is genuine and current; otherwise None."""
    if not secret or not token or token.count(".") != 1 or len(token) > 512:
        return None
    payload, sig = token.split(".")
    want = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(want, sig):
        return None
    try:
        body = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict) or not USERNAME.match(str(body.get("u", ""))):
        return None
    if int(body.get("exp", 0)) < (now or time.time()):
        return None
    if not isinstance(body.get("n"), str) or not body["n"]:
        return None
    return body

"""Writes the pass that signs a student into their online lab.

The same format as deploy/hub/handoff_token.py, which the hub uses to read
it; a test signs here and verifies there, so the two cannot drift apart
without a failure. Kept as a copy rather than an import because the hub image
does not contain this package, and this package should not depend on files
in deploy/.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

TTL = 60


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_pass(secret: str, username: str, ttl: int = TTL) -> str:
    body = {"u": username, "exp": int(time.time() + ttl), "n": secrets.token_urlsafe(12)}
    payload = _b64(json.dumps(body, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"

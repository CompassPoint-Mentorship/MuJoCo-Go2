"""JupyterHub sign-in that trusts only a pass from the classroom site.

The hub has no login page of its own. /labs/hub/login accepts a signed,
sixty-second, single-use pass (see handoff_token.py) and signs the student
in; anything else is sent to the classroom site to sign in there, which then
sends them back with a fresh pass.
"""

from __future__ import annotations

import time

from jupyterhub.auth import Authenticator
from jupyterhub.handlers import BaseHandler
from traitlets import Unicode

from handoff_token import USERNAME, read_pass

# Passes already used, by nonce, kept until they would have expired anyway.
# In memory: a hub restart forgets them, but every pass it forgets has at
# most sixty seconds left to live.
_USED: dict[str, int] = {}


def _spend(nonce: str, expires: int) -> bool:
    now = time.time()
    for n in [n for n, exp in _USED.items() if exp < now]:
        del _USED[n]
    if nonce in _USED:
        return False
    _USED[nonce] = expires
    return True


class HandoffLoginHandler(BaseHandler):
    async def get(self):
        auth = self.authenticator
        token = self.get_argument("pass", "")
        info = read_pass(auth.secret, token) if token else None
        if info and _spend(info["n"], int(info["exp"])):
            user = await self.login_user({"username": info["u"]})
            if user:
                target = self.get_argument("next", "")
                if not _safe_next(target, self.base_url):
                    target = self.get_next_url(user)
                self.redirect(target)
                return
        # No pass, or a bad one: sign in on the classroom site. "failed=1"
        # stops a loop if the two sides ever disagree about the secret.
        self.redirect(auth.classroom_url + ("?failed=1" if token else ""))


def _safe_next(target: str, base_url: str) -> bool:
    return (bool(target) and target.startswith(base_url) and "//" not in target
            and "\\" not in target and "\n" not in target and "\r" not in target)


class HandoffAuthenticator(Authenticator):
    secret = Unicode("", config=True, help="Shared with the classroom site (GO2_HUB_SECRET).")
    classroom_url = Unicode("/student/lab", config=True,
                            help="Where to send anyone who arrives without a pass.")

    auto_login = True

    def get_handlers(self, app):
        return [("/login", HandoffLoginHandler)]

    async def authenticate(self, handler, data):
        # Only HandoffLoginHandler calls this, and only with a username it
        # has just read out of a verified pass.
        name = str((data or {}).get("username", ""))
        return name if USERNAME.match(name) else None

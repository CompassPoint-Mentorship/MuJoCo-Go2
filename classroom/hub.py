"""The console's view of JupyterHub: whose online lab is running, and stopping one.

With the hub live, a student's lab is not a container the console starts: it
starts when the student opens it and stops when the idle culler decides. The
console just asks the hub. It uses a service key limited to listing users,
reading their labs' state and stopping a lab (deploy/hub/jupyterhub_config.py)
-- it cannot start, open or act inside anyone's lab.

Never raises into a page: an unreachable hub is "unknown", and the roster
still renders.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import urllib.error
import urllib.request

from .settings import Settings


class HubError(RuntimeError):
    pass


def _ago(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        when = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    minutes = int((_dt.datetime.now(_dt.timezone.utc) - when).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // 1440} days ago"


class Hub:
    def __init__(self, settings: Settings):
        self.base = os.environ.get("GO2_HUB_API_URL",
                                   "http://192.168.1.215:8600/labs/hub/api").rstrip("/")
        self.token = os.environ.get("GO2_HUB_API_TOKEN", "")
        self.enabled = bool(settings.hub_public and self.token)

    def _call(self, method: str, path: str) -> tuple[int, bytes]:
        req = urllib.request.Request(self.base + path, method=method,
                                     headers={"Authorization": f"token {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def labs(self) -> dict[str, dict] | None:
        """slug -> {state, active}. None when the hub cannot be asked."""
        if not self.enabled:
            return None
        try:
            status, body = self._call("GET", "/users")
        except (urllib.error.URLError, OSError):
            return None
        if status != 200:
            return None
        out = {}
        for user in json.loads(body):
            server = (user.get("servers") or {}).get("")
            if server and server.get("ready"):
                state = "running"
            elif user.get("pending") == "spawn" or (server and server.get("pending") == "spawn"):
                state = "starting"
            elif user.get("pending") == "stop" or (server and server.get("pending") == "stop"):
                state = "stopping"
            else:
                state = "stopped"
            active = (server or {}).get("last_activity") or user.get("last_activity")
            out[user["name"]] = {"state": state, "active": _ago(active)}
        return out

    def stop(self, slug: str) -> None:
        if not self.enabled:
            raise HubError("online labs are not switched on")
        try:
            status, body = self._call("DELETE", f"/users/{slug}/server")
        except (urllib.error.URLError, OSError) as exc:
            raise HubError(f"could not reach the hub: {exc}") from exc
        if status not in (202, 204):
            raise HubError(f"the hub answered {status}")

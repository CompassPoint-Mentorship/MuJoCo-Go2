"""Driving `deploy/go2-student.sh` from the console.

The rules for creating, starting and stopping a student's lab already exist,
are already used by hand, and already have the one dangerous operation
(removing someone) deliberately left out of them. Reimplementing any of that
in Python would give a class two sets of rules that drift apart -- so this
module shells out to the same script a mentor would type.

Which means every call here is a command line, so:

  * a student name is checked against SLUG_PATTERN before it reaches
    subprocess, and the pattern allows nothing a shell could use;
  * arguments go as a list, never through a shell;
  * every call has a timeout, because a docker build that hangs must not
    take the console with it.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

from .db import valid_slug
from .settings import Settings

# `up` builds the image the first time, which is minutes, not seconds.
TIMEOUTS = {"create": 120, "up": 900, "down": 120, "link": 30, "sync": 300}

_LINK = re.compile(r"\((?P<kind>[a-z]+)\)\s+(?P<url>https?://\S+)")


@dataclass
class Lab:
    slug: str
    port: int | None
    token: str
    work_dir: str
    status: str          # docker's own words, or "stopped"

    @property
    def running(self) -> bool:
        return self.status.lower().startswith("up")


class LabError(RuntimeError):
    """A lab command failed. The message is meant to be shown to a teacher."""


class Labs:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.deploy = settings.repo_root / "deploy"
        self.script = self.deploy / "go2-student.sh"

    # ------------------------------------------------------------- reading

    def env_file(self, slug: str):
        return self.deploy / "students" / f"{slug}.env"

    def exists(self, slug: str) -> bool:
        return self.env_file(slug).is_file()

    def info(self, slug: str) -> Lab | None:
        """What this student's lab is, read from the file that defines it.

        The env file is the source of truth for the port and token -- the
        script writes it and compose reads it -- so the console reads it too
        rather than parsing command output for facts it can look up.
        """
        if not valid_slug(slug):
            return None
        path = self.env_file(slug)
        if not path.is_file():
            return None
        values: dict[str, str] = {}
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
        port = values.get("LAB_PORT")
        return Lab(
            slug=slug,
            port=int(port) if port and port.isdigit() else None,
            token=values.get("LAB_TOKEN", ""),
            work_dir=values.get("STUDENT_WORK", ""),
            status=self._docker_status(slug),
        )

    def _docker_status(self, slug: str) -> str:
        if not self.settings.provision:
            return "stopped"
        try:
            out = subprocess.run(
                ["docker", "ps", "--filter", f"name=go2-lab-{slug}",
                 "--format", "{{.Status}}"],
                capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        return (out.stdout.strip().splitlines() or ["stopped"])[0]

    def links(self, slug: str) -> list[tuple[str, str]]:
        """The student's links, from the script that knows the addresses.

        One implementation of "which address does this host answer on" --
        the script already has it, including the tailnet case and whether a
        certificate exists, so asking it is better than guessing here.
        """
        out = self._run("link", slug, check=False)
        return [(m.group("kind"), m.group("url"))
                for m in _LINK.finditer(out)]

    # ------------------------------------------------------------- writing

    def create(self, slug: str) -> str:
        return self._run("create", slug)

    def up(self, slug: str) -> str:
        return self._run("up", slug)

    def down(self, slug: str) -> str:
        return self._run("down", slug)

    def sync(self, slug: str) -> str:
        return self._run("sync", slug)

    # ----------------------------------------------------------------- run

    def _run(self, command: str, slug: str, *, check: bool = True) -> str:
        if not valid_slug(slug):
            raise LabError(f"{slug!r} is not a valid student name")
        if not self.settings.provision:
            return ""
        if not self.script.is_file():
            raise LabError(f"cannot find {self.script}")
        try:
            out = subprocess.run(
                [str(self.script), command, slug],
                cwd=self.deploy, capture_output=True, text=True,
                timeout=TIMEOUTS.get(command, 120), check=False,
                env={**os.environ, "NO_COLOR": "1"})
        except subprocess.TimeoutExpired as exc:
            raise LabError(
                f"`go2-student.sh {command} {slug}` is still running after "
                f"{TIMEOUTS.get(command, 120)}s. It may still finish; check "
                "the server before trying again.") from exc
        except OSError as exc:
            raise LabError(f"could not run {self.script}: {exc}") from exc
        if check and out.returncode != 0:
            tail = (out.stderr or out.stdout or "").strip().splitlines()
            raise LabError(" / ".join(tail[-3:]) or
                           f"{command} failed with status {out.returncode}")
        return out.stdout

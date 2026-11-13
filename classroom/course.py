"""The class's own copy of the course material, and how a mentor changes it.

The engine ships a bundled pack inside the `go2` package. That copy is the
default and must stay pristine: it is what a student's laptop install gets,
and editing it from a web page would mean a mentor's experiment arriving on
thirty laptops the next time anyone runs the installer.

So the class works from a pack of its own -- `courses/live/` by default --
seeded from the bundled one the first time anybody looks. `GO2_COURSE_PACK`
points the engine at it, so `go2-course sync`, `go2-student.sh sync` and this
console all hand out the same files by the same rules.

Everything here is file handling driven by a browser, so every name is
rebuilt rather than trusted: the extension must be one we expect, the name is
reduced to its last component and matched against a conservative pattern, and
the result is resolved and checked to be inside the directory it belongs to.
"""

from __future__ import annotations

import pathlib
import re
import shutil
from dataclasses import dataclass

# What course material is made of. No archives, nothing executable by a
# browser, nothing that invites a student to double-click it.
ALLOWED_SUFFIXES = {".ipynb", ".py", ".md", ".txt", ".csv", ".json",
                    ".wav", ".mp3", ".ogg", ".png", ".jpg", ".jpeg", ".webp"}
MAX_FILE_BYTES = 12 * 1024 * 1024        # the dog_song.mp3 is 235 KB
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,79}$")


class CourseError(ValueError):
    """Something a mentor should read, not a traceback."""


@dataclass
class Unit:
    name: str
    size: int
    modified: float
    path: pathlib.Path

    @property
    def kind(self) -> str:
        return self.path.suffix.lstrip(".") or "file"


def safe_name(name: str) -> str:
    """The last component of `name`, if it is a name we are willing to write.

    Rebuilt, not sanitised: `..`, directories and odd bytes do not get a
    second chance to be interpreted somewhere further down.
    """
    base = pathlib.PurePosixPath(str(name).replace("\\", "/")).name.strip()
    if not base or not NAME_PATTERN.match(base):
        raise CourseError(
            f"{name!r} is not a usable file name. Letters, digits, spaces, "
            "dots and hyphens, up to 80 characters.")
    if pathlib.Path(base).suffix.lower() not in ALLOWED_SUFFIXES:
        allowed = ", ".join(sorted(s.lstrip('.') for s in ALLOWED_SUFFIXES))
        raise CourseError(f"{base} is not course material. Expected: {allowed}.")
    return base


def inside(directory: pathlib.Path, name: str) -> pathlib.Path:
    """A path for `name` that is certainly inside `directory`."""
    target = (directory / safe_name(name)).resolve()
    root = directory.resolve()
    if not target.is_relative_to(root):
        raise CourseError("that file is not in this folder")
    return target


class Pack:
    """The class's course pack: a course.toml and a units/ directory."""

    def __init__(self, path: pathlib.Path, bundled: pathlib.Path | None = None):
        self.path = pathlib.Path(path)
        self.bundled = bundled

    @property
    def units(self) -> pathlib.Path:
        return self.path / "units"

    def ensure(self) -> "Pack":
        """Create the pack from the bundled one the first time it is needed.

        Copied rather than symlinked: the point is that the class can change
        its material without changing the package, and a symlink would hand
        a mentor the installer's copy to edit.
        """
        if self.units.is_dir():
            return self
        self.units.mkdir(parents=True, exist_ok=True)
        if self.bundled and self.bundled.is_dir():
            manifest = self.bundled / "course.toml"
            if manifest.is_file():
                shutil.copy2(manifest, self.path / "course.toml")
            source = self.bundled / "units"
            source = source if source.is_dir() else self.bundled
            for item in sorted(source.iterdir()):
                if item.is_file() and not item.name.startswith("."):
                    shutil.copy2(item, self.units / item.name)
                    (self.units / item.name).chmod(0o644)
        return self

    def files(self) -> list[Unit]:
        self.ensure()
        out = []
        for path in sorted(self.units.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                stat = path.stat()
                out.append(Unit(path.name, stat.st_size, stat.st_mtime, path))
        return out

    def write(self, name: str, data: bytes) -> Unit:
        """Put a file into the pack. Replaces one of the same name."""
        if not data:
            raise CourseError("that file is empty")
        if len(data) > MAX_FILE_BYTES:
            raise CourseError(
                f"{len(data) // 1024} KB is too big; the limit is "
                f"{MAX_FILE_BYTES // 1024 // 1024} MB")
        self.ensure()
        target = inside(self.units, name)
        if target.suffix.lower() == ".ipynb":
            _check_notebook(target.name, data)
        target.write_bytes(data)
        target.chmod(0o644)
        stat = target.stat()
        return Unit(target.name, stat.st_size, stat.st_mtime, target)

    def copy_in(self, source: pathlib.Path) -> Unit:
        """Take a file a mentor wrote in their own lab into the pack."""
        return self.write(source.name, source.read_bytes())

    def remove(self, name: str) -> None:
        target = inside(self.units, name)
        if target.is_file():
            target.unlink()


def _check_notebook(name: str, data: bytes) -> None:
    """Refuse a notebook that Jupyter would refuse to open.

    A broken .ipynb handed to thirty students is thirty people stuck before
    the lesson starts, and the failure is a blank tab with no explanation --
    so it is worth one json.loads here.
    """
    import json

    try:
        nb = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CourseError(f"{name} is not a readable notebook: {exc}") from exc
    if not isinstance(nb, dict) or "cells" not in nb:
        raise CourseError(f"{name} has no cells; is it really a notebook?")

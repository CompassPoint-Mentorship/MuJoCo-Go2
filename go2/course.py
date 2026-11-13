"""Course packs: the material a class works through, and handing it out.

The engine and the course are different products on different clocks. The
simulator changes monthly; the notebooks change mid-class, on the morning
somebody finds a typo. Tying them together means a syllabus fix ships as a
new simulator, and on the hosted side as a new container image, which is
how course material stops being fixed at all.

So the engine knows nothing about any particular course. It knows how to
find a *pack* -- a directory with a `course.toml` and a `units/` folder --
and how to hand one out:

    --course <path>   >   $GO2_COURSE   >   ~/.go2/courses/<id>   >   bundled

The bundled pack is the one inside the wheel. It is the default so that a
laptop install has something to open with no network and no second step; it
is not special in any other way, and an external pack of the same shape
replaces it entirely.

Handing out is the part worth being careful about, and it is the same job on
a laptop and on the class server -- which is why it lives here once, rather
than in the launcher and again in the deployment script, where two copies
of these rules would drift apart within a term:

    missing          copy it in, read-only
    ours             replace it with the current version, read-only
    theirs           move their file to <name>.yours.<ext> and put the
                     current example back at the name it belongs to

**The material is handed over read-only** (mode 0444), and every other rule
here follows from that. A file a student cannot save over is still ours next
week, so "ours" is the normal case forever: each release replaces the example
in place, under its own name, and the folder never grows. JupyterLab shows it
as read-only, still runs every cell, and Duplicate makes a writable copy --
so "duplicate it before you change it" is one right-click, and their copy has
a name we will never touch. The notebook says so in a box under the title.

"Theirs" is the transition, not the routine: a work dir seeded before the
lock existed, or a student who went around it. Their file is never destroyed
and never silently kept either -- it moves aside, keeps its content and its
write permission, and `sync` says where it went.

Two earlier designs are worth remembering, because both failed in ways that
look like success:

  * **copy if absent.** A fix never reached anyone who had already started.
  * **leave theirs, put the new one beside it** -- first as `.updated`, then
    dated to the minute when a student who had worked in that copy stopped
    receiving updates at all. It worked, and it buried the class: four
    near-identical notebooks in a folder, and no way to tell which one the
    lesson was. Locking the example removes the need for any of it.

What a lock cannot fix: JupyterLab does not reload a notebook that is already
open, so a student with the tab open sees yesterday's lesson until they close
it. The notebook says so, and `sync` prints what it changed.

**Laptops are the primary way a class runs**, and the hosted labs are the
backup. So course material changed on the class server has to reach a laptop
without anyone re-running the installer: `pull()` mirrors the class's pack
from the classroom site into `~/.go2/courses/<id>/`, which `resolve()` already
prefers over the bundled copy, and `go2-lab` does it at every launch before
`sync()`. Offline, it says so and carries on with what is already there -- a
laptop that cannot start without the network is a laptop that fails on the
bus, at a library, or the day the site is down.

`.go2-seed` holds a copy of exactly what was last handed over. Without it
there is no way to tell our file from theirs, and the only safe rule left is
"never overwrite" -- which is the first failure above.
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request

SEED = ".go2-seed"
HANDOUT_MODE = 0o444      # read-only: the student duplicates it to edit it
SEED_MODE = 0o644
UNITS = "units"
MANIFEST = "course.toml"

PKG = pathlib.Path(__file__).resolve().parent
BUNDLED = PKG / "course_pack"

# Where the class publishes its material. One address, the same one the
# install line comes from; GO2_COURSE_URL points a laptop somewhere else.
DEFAULT_COURSE_URL = "https://robo-dog.cpmentorship.org/course"
PULL_TIMEOUT = 6.0      # seconds; a launch must not hang on a dead network


def installed_courses_dir() -> pathlib.Path:
    """Where downloaded packs live. One directory per course id."""
    root = os.environ.get("GO2_HOME") or (pathlib.Path.home() / ".go2")
    return pathlib.Path(root) / "courses"


def resolve(course: str | pathlib.Path | None = None) -> pathlib.Path:
    """Find the pack to use. Raises FileNotFoundError with a readable why."""
    candidates: list[pathlib.Path] = []
    if course:
        candidates.append(pathlib.Path(course).expanduser())
    elif os.environ.get("GO2_COURSE"):
        candidates.append(pathlib.Path(os.environ["GO2_COURSE"]).expanduser())
    else:
        # The most recently written installed pack, then the bundled one.
        courses = installed_courses_dir()
        if courses.is_dir():
            packs = [p for p in courses.iterdir() if (p / UNITS).is_dir()]
            candidates += sorted(packs, key=lambda p: p.stat().st_mtime, reverse=True)
        candidates.append(BUNDLED)

    for path in candidates:
        if (path / UNITS).is_dir():
            return path
        if path.is_dir() and any(path.iterdir()):
            # A pack that predates the units/ layout, or a plain folder of
            # notebooks someone pointed us at. Take it at its word.
            return path
    raise FileNotFoundError(
        f"no course material at {candidates[0] if candidates else '(nowhere)'}.\n"
        "Point --course or $GO2_COURSE at a folder holding course.toml and units/."
    )


def units_dir(pack: pathlib.Path) -> pathlib.Path:
    """The files to hand out. A flat pack is its own units directory."""
    inner = pack / UNITS
    return inner if inner.is_dir() else pack


def manifest(pack: pathlib.Path) -> dict:
    """Read course.toml. A pack without one still works; it is just anonymous.

    A broken or unreadable manifest is survivable -- the units are files on
    disk and sync() hands them over regardless -- so this reports and carries
    on rather than stopping a class over metadata.
    """
    import tomllib

    path = pack / MANIFEST
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except Exception as exc:  # noqa: BLE001 - a broken manifest must not stop a class
        print(f"[go2] ignoring an unreadable {MANIFEST}: {exc}", file=sys.stderr)
        return {}


def describe(pack: pathlib.Path) -> str:
    """One line naming the course, for a banner or a bug report."""
    meta = manifest(pack).get("course", {})
    if not meta:
        return f"course at {pack}"
    return f"{meta.get('title', meta.get('id', 'course'))} {meta.get('version', '')}".strip()


def course_id(pack: pathlib.Path) -> str:
    return str(manifest(pack).get("course", {}).get("id", pack.name))


def _place(src: pathlib.Path, dst: pathlib.Path, mode: int) -> None:
    """Put `src` at `dst` with the mode we want, replacing whatever is there.

    Unlink first: the file we are replacing is usually read-only, which is
    the whole point, and copying onto it would fail. Removing it needs write
    permission on the directory, not on the file.
    """
    dst.unlink(missing_ok=True)
    shutil.copy2(src, dst)
    dst.chmod(mode)


def _same(a: pathlib.Path, b: pathlib.Path) -> bool:
    try:
        return filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def sync(work: pathlib.Path, pack: pathlib.Path | None = None,
         dry_run: bool = False) -> list[str]:
    """Hand the pack to one student, without ever eating their work.

    Returns one line per thing done -- empty when there was nothing to do,
    which is the common case and should stay quiet.
    """
    pack = pack or resolve()
    src_dir = units_dir(pack)
    work = pathlib.Path(work)
    if not dry_run:
        work.mkdir(parents=True, exist_ok=True)
    seed = work / SEED
    if not dry_run:
        seed.mkdir(exist_ok=True)

    notes: list[str] = []
    for src in sorted(src_dir.iterdir()):
        if src.name.startswith((".", "__")) or src.is_dir():
            continue
        dst = work / src.name
        kept = seed / src.name

        if not dst.exists():
            if not dry_run:
                _place(src, dst, HANDOUT_MODE)
                _place(src, kept, SEED_MODE)
            notes.append(f"added {src.name}")
        elif _same(src, dst):
            if not dry_run:
                dst.chmod(HANDOUT_MODE)          # lock copies handed out before
                _place(src, kept, SEED_MODE)     # backfill for older work dirs
        elif kept.exists() and _same(kept, dst):
            if not dry_run:
                _place(src, dst, HANDOUT_MODE)
                _place(src, kept, SEED_MODE)
            notes.append(f"updated {src.name} (you had not changed it)")
        else:
            # Theirs. The example is replaced every release, so their work
            # moves to a name of its own rather than standing in the way of
            # it -- and keeps the write permission that made it theirs.
            rescue = _rescue_name(work, src)
            if not dry_run:
                dst.rename(rescue)
                rescue.chmod(0o644)
                _place(src, dst, HANDOUT_MODE)
                _place(src, kept, SEED_MODE)
            notes.append(f"your {src.name} is now {rescue.name}; "
                         f"{src.name} is the current example again")

        # Copies the old dated scheme left behind. The example at its own
        # name is current now, so any of ours are superseded by definition;
        # ones the student worked in are theirs and are left where they are.
        stale = _untouched_copies(_spare_copies(work, src), seed)
        if stale:
            if not dry_run:
                for old_copy in stale:
                    old_copy.unlink()
                    (seed / old_copy.name).unlink(missing_ok=True)
            notes.append(
                f"  (removed {len(stale)} older cop"
                f"{'y' if len(stale) == 1 else 'ies'} of {src.name} "
                "you had not changed)")
    return notes


def _spare_copies(work: pathlib.Path, src: pathlib.Path) -> list[pathlib.Path]:
    """Copies of `src` that older releases left beside a student's own file.

    `.updated`, `.updated-<date>-<time>` and `.new` are all names this code
    used to hand material out under. Nothing creates them any more, so they
    are only ever cleaned up -- and only when untouched, which is what makes
    a folder that collected a term of them empty itself again.
    """
    return sorted(set(work.glob(f"{src.stem}.new{src.suffix}"))
                  | set(work.glob(f"{src.stem}.updated*{src.suffix}")))


def _rescue_name(work: pathlib.Path, src: pathlib.Path) -> pathlib.Path:
    """Where a student's own version of `src` goes when the example returns.

    `.yours` says whose it is in the file browser, which is where they will
    look for it. Numbered only if one is already there -- which means they
    unlocked the example and edited it a second time.
    """
    candidate = work / f"{src.stem}.yours{src.suffix}"
    n = 2
    while candidate.exists():
        candidate = work / f"{src.stem}.yours-{n}{src.suffix}"
        n += 1
    return candidate


def _untouched_copies(handed_over: list[pathlib.Path],
                      seed: pathlib.Path) -> list[pathlib.Path]:
    """Earlier update copies the student never changed.

    Only ever files we wrote ourselves, identified by still matching the copy
    kept in the seed. A file the student has edited never matches, so their
    work is never a candidate for tidying.
    """
    return [p for p in handed_over
            if (seed / p.name).is_file() and _same(seed / p.name, p)]


# ---------------------------------------------------------------------------
# pulling the class's pack onto a laptop
# ---------------------------------------------------------------------------

class PullResult:
    """What a pull did, in words a student can read in a terminal."""

    def __init__(self) -> None:
        self.notes: list[str] = []
        self.pack: pathlib.Path | None = None
        self.online = False
        self.engine_commit = ""

    def __bool__(self) -> bool:
        return self.online


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "go2-course"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def course_url() -> str:
    """Where to pull from; "off" turns pulling off for a laptop."""
    return os.environ.get("GO2_COURSE_URL", DEFAULT_COURSE_URL).rstrip("/")


def pull(url: str | None = None, timeout: float = PULL_TIMEOUT,
         root: pathlib.Path | None = None) -> PullResult:
    """Mirror the class's published pack into ~/.go2/courses/<id>/.

    Never raises for a network problem: an unreachable site is reported and
    the laptop carries on with the material it already has. Only files whose
    hash differs are downloaded, and the mirror is updated by writing beside
    and renaming over, so an interrupted pull never leaves half a notebook.

    The mirror is ours, not the student's -- their work is in the notebook
    folder, which sync() still guards -- so it follows the server exactly,
    including files the class has removed.
    """
    result = PullResult()
    base = (url or course_url()).rstrip("/")
    if base.lower() in ("", "off", "none"):
        return result
    try:
        manifest = json.loads(_fetch(f"{base}/manifest.json", timeout))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        result.notes.append(
            f"offline or the class site did not answer ({reason}); "
            "using the course material already on this computer")
        return result

    course_id = str(manifest.get("id") or "course")
    if not course_id.replace("-", "").replace("_", "").isalnum():
        result.notes.append(f"the class site sent an odd course id {course_id!r}; ignored")
        return result
    files = manifest.get("files") or []
    result.online = True
    result.engine_commit = str((manifest.get("engine") or {}).get("commit") or "")

    dest = (root or installed_courses_dir()) / course_id
    units = dest / UNITS
    units.mkdir(parents=True, exist_ok=True)

    wanted: dict[str, str] = {}
    for entry in files:
        name = pathlib.PurePosixPath(str(entry.get("name", ""))).name
        sha = str(entry.get("sha256", ""))
        if not name or name.startswith(".") or len(sha) != 64:
            continue
        wanted[name] = sha

    fetched = 0
    for name, sha in sorted(wanted.items()):
        target = units / name
        if target.is_file() and _sha256(target) == sha:
            continue
        try:
            # The hash is in the URL so that no cache between here and the
            # site -- Cloudflare caches .mp3 and .csv on sight -- can answer
            # with yesterday's bytes. The check below would catch it anyway.
            data = _fetch(f"{base}/files/{urllib.request.quote(name)}?v={sha[:16]}",
                          timeout * 3)
        except (urllib.error.URLError, OSError) as exc:
            result.notes.append(f"could not download {name} ({exc}); kept the old copy")
            continue
        if hashlib.sha256(data).hexdigest() != sha:
            result.notes.append(f"{name} arrived damaged; kept the old copy")
            continue
        with tempfile.NamedTemporaryFile(dir=units, delete=False,
                                         prefix=".incoming-") as tmp:
            tmp.write(data)
        os.replace(tmp.name, target)
        fetched += 1

    removed = 0
    for path in units.iterdir():
        if path.is_file() and not path.name.startswith(".") and path.name not in wanted:
            path.unlink()
            removed += 1

    if manifest.get("course_toml"):
        (dest / MANIFEST).write_text(str(manifest["course_toml"]))
    os.utime(dest)        # resolve() prefers the most recently written pack

    if fetched or removed:
        bits = []
        if fetched:
            bits.append(f"{fetched} new or changed")
        if removed:
            bits.append(f"{removed} withdrawn")
        result.notes.append(f"course material from the class: {', '.join(bits)}")
    result.pack = dest
    return result


def check(url: str | None = None, timeout: float = PULL_TIMEOUT,
          root: pathlib.Path | None = None) -> dict:
    """Is there anything new for this laptop? Looks, downloads nothing.

    Compares the class site's hashes with the mirror on this laptop. Cheap
    enough to run every few minutes -- a manifest is a few hundred bytes --
    which is what lets a lab that has been open all week say "there is
    something new" instead of waiting to be restarted.
    """
    status = {"online": False, "new": [], "withdrawn": [], "checked": time.time()}
    base = (url or course_url()).rstrip("/")
    if base.lower() in ("", "off", "none"):
        return status
    try:
        manifest = json.loads(_fetch(f"{base}/manifest.json", timeout))
    except (urllib.error.URLError, OSError, ValueError):
        return status
    course_id = str(manifest.get("id") or "course")
    units = (root or installed_courses_dir()) / course_id / UNITS
    wanted = {pathlib.PurePosixPath(str(e.get("name", ""))).name: str(e.get("sha256", ""))
              for e in manifest.get("files") or []}
    wanted.pop("", None)
    for name, sha in sorted(wanted.items()):
        target = units / name
        if not (target.is_file() and _sha256(target) == sha):
            status["new"].append(name)
    if units.is_dir():
        status["withdrawn"] = sorted(
            p.name for p in units.iterdir()
            if p.is_file() and not p.name.startswith(".") and p.name not in wanted)
    status["online"] = True
    status["engine_behind"] = engine_is_behind(
        str((manifest.get("engine") or {}).get("commit") or ""))
    return status


class Watcher:
    """Keeps an eye on the class site for a lab that stays open.

    A student opens go2-lab on Monday and closes it on Friday; pulling only
    at launch means a mentor's Wednesday fix reaches them next week. This
    checks in the background and remembers the answer, so the panel can
    show a flag -- but it never applies anything by itself. JupyterLab does
    not reload an open notebook, so replacing one under an open tab is
    invisible; the student clicks, and is told what arrived and what to
    reopen.
    """

    def __init__(self, work: pathlib.Path, url: str | None = None,
                 every: float = 600.0, root: pathlib.Path | None = None):
        import threading

        self.work = pathlib.Path(work)
        self.url = url
        self.every = every
        self.root = root
        self._lock = threading.Lock()
        self._status: dict = {"online": False, "new": [], "withdrawn": [],
                              "checked": 0.0}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "Watcher":
        import threading

        self._thread = threading.Thread(target=self._loop, name="course-watch",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(self.every)

    def refresh(self) -> dict:
        found = check(self.url, root=self.root)
        with self._lock:
            # An offline check keeps the last good answer rather than
            # pretending there is nothing new.
            if found["online"] or not self._status["checked"]:
                self._status = found
        return self.status()

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def update_now(self) -> list[str]:
        """Fetch what is new and hand it over. Returns lines to show."""
        pulled = pull(self.url, root=self.root)
        notes = list(pulled.notes)
        if pulled.online:
            notes += sync(self.work, resolve())
        self.refresh()
        return notes or ["already up to date"]


def engine_is_behind(server_commit: str) -> bool:
    """True when this laptop's simulator is older than the class's.

    Only an announcement: replacing the engine is a 250 MB download, and the
    start of a lesson is the worst moment to do one unasked. The student is
    told, and runs the install line when it suits them.
    """
    if not server_commit:
        return False
    try:
        from .build import build_info
    except Exception:  # noqa: BLE001
        return False
    mine = build_info().get("commit", "")
    return bool(mine) and not server_commit.startswith(mine[:7]) \
        and not mine.startswith(server_commit[:7])


# ---------------------------------------------------------------------------
# go2-course
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="go2-course",
        description="Show or hand out the course material.",
    )
    ap.add_argument("--course", default=None,
                    help="pack to use (default: $GO2_COURSE, an installed pack, or the bundled one)")
    sub = ap.add_subparsers(dest="cmd")

    show = sub.add_parser("show", help="what this pack is, and what is in it")
    show.add_argument("--files", action="store_true", help="list the files too")

    pul = sub.add_parser("pull", help="fetch the class's latest material from the class site")
    pul.add_argument("--from", dest="url", default=None,
                     help=f"the class site's course URL (default: $GO2_COURSE_URL or {DEFAULT_COURSE_URL})")

    syn = sub.add_parser("sync", help="hand it out, without overwriting student work")
    syn.add_argument("--into", required=True, help="the student's work directory")
    syn.add_argument("-n", "--dry-run", action="store_true",
                     help="say what would happen; write nothing")

    args = ap.parse_args(argv)

    if args.cmd == "pull":
        result = pull(args.url)
        for note in result.notes or ["already up to date"]:
            print(f"  {note}")
        if result.pack:
            print(f"  pack     {result.pack}")
        return 0

    try:
        pack = resolve(args.course)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.cmd == "sync":
        notes = sync(pathlib.Path(args.into).expanduser(), pack, dry_run=args.dry_run)
        if args.dry_run:
            print("(dry run: nothing written)")
        for note in notes:
            print(f"  {note}")
        if not notes:
            print("  already up to date")
        return 0

    # show, and the default when nothing is asked for
    meta = manifest(pack).get("course", {})
    print(describe(pack))
    print(f"  pack     {pack}")
    if meta.get("summary"):
        print(f"  {meta['summary'].strip()}")
    for i, unit in enumerate(manifest(pack).get("unit", []), 1):
        print(f"  {i}. {unit.get('title', unit.get('id', '?'))}"
              f"{'  -- ' + unit['goal'] if unit.get('goal') else ''}")
        if args.files:
            for name in unit.get("files", []):
                there = "ok " if (units_dir(pack) / name).is_file() else "MISSING"
                print(f"       [{there}] {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

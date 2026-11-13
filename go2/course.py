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

    missing          copy it in
    same as seed     never edited, so replace it
    anything else    leave it; the new version lands beside it as
                     <name>.updated-<date>-<time>.<ext>

The timestamp is not decoration. A fixed name has two failure modes, and
both of them lose work silently:

  * once a student runs a cell in the .updated copy, that copy is theirs
    too, and the next update has nowhere to go. The old code noticed and
    skipped it -- so a fix stopped reaching exactly the students who were
    furthest along.
  * replacing a file in place is invisible. JupyterLab does not reload a
    notebook that is already open, so a student with the tab open sees
    yesterday's lesson and no sign that anything happened.

A name nothing is using solves both: the update always lands somewhere, and
it lands as a NEW file in the file browser, which is the only signal a
student actually sees. Copies we delivered and nobody touched are tidied up
as they are superseded, so the folder does not fill with them.

`.go2-seed` holds a copy of exactly what was last handed over. Without it
there is no way to tell an edited file from an untouched one, and the only
safe rule left is "never overwrite" -- which means a fix never reaches
anyone who has already started.
"""

from __future__ import annotations

import argparse
import filecmp
import os
import pathlib
import shutil
import sys
import time

SEED = ".go2-seed"
UNITS = "units"
MANIFEST = "course.toml"

PKG = pathlib.Path(__file__).resolve().parent
BUNDLED = PKG / "course_pack"


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
                shutil.copy2(src, dst)
                shutil.copy2(src, kept)
            notes.append(f"added {src.name}")
        elif _same(src, dst):
            if not dry_run:
                shutil.copy2(src, kept)          # backfill for older work dirs
        elif kept.exists() and _same(kept, dst):
            if not dry_run:
                shutil.copy2(src, dst)
                shutil.copy2(src, kept)
            notes.append(f"updated {src.name} (you had not changed it)")
        else:
            # Their copy differs from what we handed over, so it is theirs and
            # it stays. The new version arrives under a name nothing is using.
            handed_over = sorted(work.glob(f"{src.stem}.updated*{src.suffix}"))
            if any(_same(src, old) for old in handed_over):
                pass                      # already delivered, under some name
            else:
                fresh = _update_name(work, src)
                superseded = _untouched_copies(handed_over, seed)
                if not dry_run:
                    shutil.copy2(src, fresh)
                    shutil.copy2(src, seed / fresh.name)
                    for stale in superseded:
                        stale.unlink()
                        (seed / stale.name).unlink(missing_ok=True)
                notes.append(f"kept your {src.name}; the new one is {fresh.name}")
                if superseded:
                    notes.append(
                        f"  (removed {len(superseded)} earlier cop"
                        f"{'y' if len(superseded) == 1 else 'ies'} "
                        "you had not changed)")
            if not dry_run:
                shutil.copy2(src, kept)
    return notes


def _update_name(work: pathlib.Path, src: pathlib.Path) -> pathlib.Path:
    """A name for the new copy that nothing in `work` is using.

    Dated to the minute, so the newest one sorts last in the file browser and
    a student can tell at a glance which is the one to open.
    """
    base = f"{src.stem}.updated-{time.strftime('%Y%m%d-%H%M')}"
    candidate = work / f"{base}{src.suffix}"
    n = 2
    while candidate.exists():            # two fixes in the same minute
        candidate = work / f"{base}-{n}{src.suffix}"
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

    syn = sub.add_parser("sync", help="hand it out, without overwriting student work")
    syn.add_argument("--into", required=True, help="the student's work directory")
    syn.add_argument("-n", "--dry-run", action="store_true",
                     help="say what would happen; write nothing")

    args = ap.parse_args(argv)
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
